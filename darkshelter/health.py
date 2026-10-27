"""Health, mode ladder and thread watchdog (SPEC 6.10).

Mode precedence: forced > PAPER > TYPED > FAST > FULL.
The watchdog restarts a thread only if it is dead (never on a stale heartbeat), so a
second worker can never run ASR and the LLM at the same time.
"""
import shutil
import sys
import threading
import time

from . import textnorm
from .config import resolve_path

MODES = ("FULL", "FAST", "TYPED", "PAPER")

REASONS = {
    "forced": ("值班人員手動切換", "Set manually"),
    "battery_paper": ("電量低於5%，改用紙本", "Battery below 5%: paper mode"),
    "asr_loading": ("語音模型載入中，先用打字", "Speech model loading: typing for now"),
    "asr_failed": ("語音模型無法載入，改用打字", "Speech model failed: typing only"),
    "no_mic": ("麥克風無法使用，改用打字", "No microphone: typing only"),
    "llm_down": ("AI 模型未執行，只用規則（可手動請 AI）", "AI model not running: rules only"),
    "llm_slow": ("AI 太慢，只在需要時使用", "AI too slow: on request only"),
    "llm_warming": ("AI 暖機中", "AI warming up"),
    "llm_failed": ("AI 暖機失敗（記憶體不足或模型錯誤），先只用規則，每分鐘自動重試",
                   "AI warm-up failed (memory or model error): rules only, retrying every minute"),
    "backlog": ("排隊太多，先用快速模式", "Backlog: fast mode"),
    "battery_low": ("電量低於15%，節能模式", "Battery below 15%: economy mode"),
    "ok": ("一切正常", "All systems normal"),
}


def battery_status():
    """{'percent', 'plugged'} or None (psutil optional)."""
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return None
        return {"percent": float(b.percent), "plugged": bool(b.power_plugged)}
    except Exception:
        return None


class Health:
    def __init__(self, cfg, store, pipeline=None, asr=None, llm=None, mock=False, clock=None):
        self.cfg = cfg
        self.store = store
        self.pipeline = pipeline
        self.asr = asr
        self.llm = llm
        self.mock = mock
        self.clock = clock or time.time
        self.forced = None
        self.mic_ok = True
        self.asr_ok = None       # None = loading
        self.asr_error = None
        self.asr_model = None
        self.llm_ok = False
        self.llm_ready = False   # warm-up finished
        self.llm_slow = False
        self.llm_tps = None
        self.prefix_cached = None
        self.llm_error = None
        self.llm_warm_failed = False
        self._warm_pending = False
        self._next_warm = 0.0
        self._llm_timeouts = 0       # LLM requests that timed out in a row
        self._slow_probe = False     # llm_slow because of timeouts: a short probe retries every 60 s
        self.asr_fallback = False
        self.battery = None
        self.disk_free_mb = None
        self.warnings = []
        self.threads = {}
        self._lock = threading.Lock()
        self._mode = ("TYPED", "asr_loading")
        self._stop = threading.Event()
        self.thread = None
        self._last_error_log = {}
        self._startup_done = threading.Event()

    # ------------------------------------------------------------ watchdog registry
    def register(self, name, get_thread, restart=None, stale_s=10.0):
        with self._lock:
            self.threads[name] = {"get": get_thread, "restart": restart, "stale_s": float(stale_s),
                                  "beat": time.monotonic(), "restarts": 0, "light": "green", "note": ""}

    def beat(self, name):
        t = self.threads.get(name)
        if t is not None:
            t["beat"] = time.monotonic()

    def beater(self, name):
        return lambda: self.beat(name)

    def watchdog(self):
        now = time.monotonic()
        for name, t in list(self.threads.items()):
            th = t["get"]()
            alive = bool(th is not None and th.is_alive())
            if not alive:
                t["light"] = "red"
                t["note"] = "dead"
                if t["restart"] is not None:
                    try:
                        t["restart"]()
                        t["restarts"] += 1
                        t["beat"] = time.monotonic()
                        self._event("thread_restarted", {"thread": name, "restarts": t["restarts"]})
                    except Exception as e:
                        self._event("thread_restart_failed", {"thread": name, "error": str(e)})
                continue
            age = now - t["beat"]
            if age > t["stale_s"]:
                if t["light"] != "amber":
                    self._event("thread_stale", {"thread": name, "age_s": round(age, 1)})
                t["light"] = "amber"
                t["note"] = f"stale {age:.0f}s"
            else:
                t["light"] = "green" if t["restarts"] == 0 else "green"
                t["note"] = ""

    def _event(self, etype, data):
        try:
            self.store.log_event(etype, data)
        except Exception:
            pass

    # ------------------------------------------------------------ inputs from other parts
    def set_forced(self, mode):
        if mode is not None and mode not in MODES:
            raise ValueError(f"bad mode {mode}")
        self.forced = mode
        self._event("mode_forced", {"mode": mode})
        self.recompute()

    def set_mic(self, ok):
        self.mic_ok = bool(ok)
        self.recompute()

    def on_warmup(self, res):
        self._warm_pending = False
        if res.get("probe") and self._slow_probe and (res.get("ok") or res.get("error") == "timeout"):
            # Probe after timeouts (red-team M8): the model is loaded, only slow. Clear on a timely answer,
            # otherwise stay FAST/llm_slow and probe again in 60 s.
            min_tps = float(self.cfg["llm"].get("min_tokens_per_s", 4))
            tps = res.get("tokens_per_s")
            if res.get("ok") and (tps is None or tps >= min_tps):
                self._slow_probe = False
                self.llm_slow = False
                self._llm_timeouts = 0
                if tps:
                    self.llm_tps = tps
                self._event("llm_recovered", {"tokens_per_s": tps, "seconds": res.get("seconds")})
            else:
                self._next_warm = time.monotonic() + 60.0
            self.recompute()
            return
        self._slow_probe = False
        self._llm_timeouts = 0
        self.llm_ready = bool(res.get("ok"))
        self.llm_warm_failed = not self.llm_ready
        if not self.llm_ready:
            self._next_warm = time.monotonic() + 60.0
            self._event("llm_warmup_failed", {"error": res.get("error"), "detail": str(res.get("detail") or "")[:300]})
        self.llm_tps = res.get("tokens_per_s")
        self.prefix_cached = res.get("prefix_cached")
        self.llm_error = res.get("error")
        min_tps = float(self.cfg["llm"].get("min_tokens_per_s", 4))
        self.llm_slow = bool(self.llm_ready and self.llm_tps is not None and self.llm_tps < min_tps)
        if self.llm_ready:
            self.llm_ok = True
        self.recompute()

    def on_llm_result(self, res):
        if res.get("tokens_per_s"):
            self.llm_tps = res["tokens_per_s"]
        if res.get("error") in ("llm_error",):
            self.llm_error = res["error"]
        # Red-team M8: a hung LLM (Ollama stalls while /api/tags still answers) made every queued intake
        # wait one full timeout. Two timeouts in a row -> FAST (llm_slow); the 60 s probe clears it.
        if res.get("error") == "timeout":
            self._llm_timeouts += 1
            if self._llm_timeouts >= 2 and not self._slow_probe:
                self._slow_probe = True
                self.llm_slow = True
                self._next_warm = time.monotonic() + 60.0
                self._event("llm_slow", {"timeouts_in_a_row": self._llm_timeouts})
                self.recompute()
        else:
            self._llm_timeouts = 0

    def on_asr_error(self, exc):
        self.asr_error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ mode
    def fast_condition(self):
        """True when the LLM must not run automatically (FAST conditions)."""
        if not self.llm_ok or not self.llm_ready or self.llm_slow:
            return True
        if self.pipeline is not None and self.pipeline.backlog() >= int(self.cfg["pipeline"].get("backlog_fast_mode", 3)):
            return True
        b = self.battery
        if b and b["percent"] < 15 and not b["plugged"]:
            return True
        return False

    def recompute(self):
        b = self.battery
        if self.forced:
            m = (self.forced, "forced")
        elif b and b["percent"] < 5 and not b["plugged"]:
            m = ("PAPER", "battery_paper")
        elif self.asr_ok is None:
            m = ("TYPED", "asr_loading")
        elif self.asr_ok is False:
            m = ("TYPED", "asr_failed")
        elif not self.mic_ok:
            m = ("TYPED", "no_mic")
        elif not self.llm_ok:
            m = ("FAST", "llm_down")
        elif not self.llm_ready:
            m = ("FAST", "llm_failed" if self.llm_warm_failed else "llm_warming")
        elif self.llm_slow:
            m = ("FAST", "llm_slow")
        elif self.pipeline is not None and self.pipeline.backlog() >= int(self.cfg["pipeline"].get("backlog_fast_mode", 3)):
            m = ("FAST", "backlog")
        elif b and b["percent"] < 15 and not b["plugged"]:
            m = ("FAST", "battery_low")
        else:
            m = ("FULL", "ok")
        if m != self._mode:
            self._event("mode_changed", {"mode": m[0], "reason": m[1]})
        self._mode = m
        return m[0]

    def mode(self):
        return self._mode[0]

    # ------------------------------------------------------------ startup + loop
    def start(self):
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="health", daemon=True)
        self.thread.start()
        return self.thread

    def stop(self):
        self._stop.set()

    def startup_checks(self):
        w = []
        if sys.version_info < (3, 11):
            w.append(f"Python {sys.version.split()[0]} < 3.11")
        if not textnorm.has_opencc():
            w.append("OpenCC 未安裝：簡體字不會轉成繁體（比對仍可用） / OpenCC missing")
        try:
            import psutil
            avail_gb = psutil.virtual_memory().available / 1024 ** 3
            if avail_gb < 1.5:
                w.append(f"可用記憶體只剩 {avail_gb:.1f} GB：請關閉瀏覽器分頁或其他程式，語音模型可能無法載入"
                         f" / only {avail_gb:.1f} GB RAM free: close browser tabs or other apps")
        except Exception:
            w.append("psutil 未安裝：無法讀取電量 / psutil missing (no battery reading)")
        try:
            db = resolve_path(self.cfg["paths"]["desk_db"])
            self.disk_free_mb = int(shutil.disk_usage(str(db.parent)).free // (1024 * 1024))
            if self.disk_free_mb < 200:
                w.append(f"磁碟空間不足 {self.disk_free_mb} MB / low disk")
        except Exception as e:
            w.append(f"disk check failed: {e}")
        try:
            self.store.log_event("health_startup", {"warnings": w})
        except Exception as e:
            w.append(f"資料庫無法寫入 / DB not writable: {e}")
        self.battery = battery_status()
        self.warnings = w
        # ASR load (local files only)
        if self.asr is None:
            self.asr_ok = False
            self.asr_error = "no ASR configured"
        else:
            try:
                self.asr.load(fast=False)
                self.asr_ok = True
                self.asr_model = getattr(self.asr, "model_name", None)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                fast_name = getattr(self.asr, "fast_model_name", None)
                try:
                    # Fail soft: the smaller FAST model (base) instead of no speech at all.
                    self.asr.load(fast=True)
                    self.asr_ok = True
                    self.asr_fallback = True
                    self.asr_model = fast_name
                    self.asr_error = err
                    self.warnings.append(f"主要語音模型無法載入，改用較小的 {fast_name} / main ASR model failed, "
                                         f"using {fast_name}: {err}"[:300])
                    self._event("asr_fallback", {"error": err, "model": fast_name})
                except Exception as e2:
                    self.asr_ok = False
                    self.asr_error = f"{err}; fallback: {type(e2).__name__}: {e2}"
                    self.warnings.append(f"語音模型載入失敗 / ASR failed: {self.asr_error}"[:300])
        # LLM: availability now, warm-up as a pipeline job (never from this thread)
        self._check_llm(initial=True)
        self.recompute()
        self._startup_done.set()

    def _check_llm(self, initial=False):
        if self.llm is None or not self.cfg["llm"].get("enabled", True):
            self.llm_ok = False
            return
        try:
            ok = bool(self.llm.available())
        except Exception:
            ok = False
        was = self.llm_ok
        self.llm_ok = ok
        if ok and (initial or not was):
            self.llm_ready = False
            self.llm_warm_failed = False
            self._request_warmup()
        elif ok and not self.llm_ready and not self._warm_pending and time.monotonic() >= self._next_warm:
            self._request_warmup()  # retry a failed warm-up (e.g. not enough memory to load the model)
        elif ok and self._slow_probe and not self._warm_pending and time.monotonic() >= self._next_warm:
            self._request_warmup(probe=True)  # slow after timeouts: does it answer in time again?
        if not ok:
            self.llm_ready = False  # the full warm-up when it is back recomputes llm_slow
            self._slow_probe = False
            self._llm_timeouts = 0

    def _request_warmup(self, probe=False):
        """Warm-up runs as a pipeline job (never on this thread, never overlapping Whisper)."""
        if self.pipeline is not None and not self._warm_pending:
            self._warm_pending = True
            if probe:
                try:
                    self.pipeline.submit_warmup(probe=True)
                    return
                except TypeError:  # a pipeline without probes: use the full warm-up
                    pass
            self.pipeline.submit_warmup()

    def _run(self):
        try:
            self.startup_checks()
        except Exception as e:
            self._log_error("health_error", e)
        last_full = 0.0
        while not self._stop.is_set():
            try:
                self.beat("health")
                now = time.monotonic()
                self.watchdog()
                if now - last_full >= 10.0:
                    last_full = now
                    self.battery = battery_status()
                    self._check_llm()
                self.recompute()
            except Exception as e:
                self._log_error("health_error", e)
            self._stop.wait(1.0)

    def _log_error(self, etype, exc):
        now = time.monotonic()
        if now - self._last_error_log.get(etype, -1e9) < 60:
            return
        self._last_error_log[etype] = now
        self._event(etype, {"error": f"{type(exc).__name__}: {exc}"})

    def status(self):
        mode, reason = self._mode
        zh, en = REASONS.get(reason, (reason, reason))
        b = self.battery
        return {
            "mode": mode, "reason": reason, "reason_zh": zh, "reason_en": en, "forced": self.forced,
            "asr_ok": self.asr_ok, "asr_model": self.asr_model, "asr_error": self.asr_error,
            "llm_ok": self.llm_ok, "llm_ready": self.llm_ready, "llm_slow": self.llm_slow,
            "llm_tps": round(self.llm_tps, 1) if self.llm_tps else None, "prefix_cached": self.prefix_cached,
            "llm_error": self.llm_error, "opencc": textnorm.has_opencc(), "mic_ok": self.mic_ok,
            "battery": None if b is None else round(b["percent"]), "charging": None if b is None else b["plugged"],
            "disk_free_mb": self.disk_free_mb, "mock": self.mock,
            "threads": {n: {"light": t["light"], "restarts": t["restarts"], "note": t["note"]}
                        for n, t in self.threads.items()},
            "warnings": list(self.warnings),
        }
