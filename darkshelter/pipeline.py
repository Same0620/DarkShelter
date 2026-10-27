"""Intake pipeline: one worker thread, one priority queue, never ASR and LLM at once.

Stages: queued -> checking -> transcribing -> understanding -> refining -> done | error.
Rule drafts are created immediately; the LLM only refines them when the mode allows.
"""
import copy
import itertools
import queue
import threading
import time
import uuid
from collections import OrderedDict

from . import codebook
from . import dedupe
from . import extract
from . import triage
from .store import Conflict

PRIO_INTAKE = 0
PRIO_WARMUP = 1

SCREENING_QUESTIONS = {
    "q1": "有人需要洗腎、氧氣或用電的醫療器材嗎？",
    "q2": "有人每天一定要吃的藥（例如胰島素）嗎？",
    "q3": "有嬰幼兒、孕婦或行動不便的人嗎？",
    "q4": "有家人失聯，或還有人在家裡沒出來嗎？",
    "q5": "還有其他需要幫忙的嗎（寵物、聯絡家人）？",
}

WARN = {
    "too_short": {"code": "too_short", "zh": "錄音太短，請再錄一次", "en": "Recording too short"},
    "silent": {"code": "silent", "zh": "沒有聽到聲音", "en": "No speech heard"},
    "no_text": {"code": "no_text", "zh": "沒有聽到清楚的話", "en": "Nothing clear was heard"},
    "clipped": {"code": "clipped", "zh": "音量太大有破音，請離麥克風遠一點", "en": "Audio clipped"},
    "too_long": {"code": "too_long", "zh": "錄音超過上限，只處理前段", "en": "Recording truncated"},
    "bad_audio": {"code": "bad_audio", "zh": "無法讀取錄音檔", "en": "Could not decode the audio"},
    "asr_failed": {"code": "asr_failed", "zh": "語音辨識失敗，請改用打字", "en": "Speech recognition failed; please type"},
    "asr_low_conf": {"code": "asr_low_conf", "zh": "語音辨識信心偏低，請核對逐字稿", "en": "Low ASR confidence"},
    "llm_timeout": {"code": "llm_timeout", "zh": "AI 逾時，保留規則草稿", "en": "AI timed out; rule draft kept"},
    "llm_error": {"code": "llm_error", "zh": "AI 錯誤，保留規則草稿", "en": "AI error; rule draft kept"},
    "empty": {"code": "empty", "zh": "沒有內容", "en": "Empty input"},
}

AI_FIELDS = ("category", "item", "item_code", "quantity", "quantity_approx", "subject", "subject_group",
             "location", "zone_code", "time_phrase", "deadline_at", "deadline_text", "emergency",
             "priority", "rule_priority", "priority_reasons", "missing_info", "evidence", "summary")


class Pipeline:
    def __init__(self, store, cfg, asr=None, llm=None, health=None, clock=None, beat=None):
        self.store = store
        self.cfg = cfg
        self.asr = asr
        self.llm = llm
        self.health = health
        self.clock = clock or time.time
        self.beat = beat or (lambda: None)
        self.q = queue.PriorityQueue()
        self._counter = itertools.count()
        self.jobs = OrderedDict()
        self._jobs_lock = threading.Lock()
        self._stop = threading.Event()
        self.thread = None
        self._last_error_log = {}
        self.current_job = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="worker", daemon=True)
        self.thread.start()
        return self.thread

    def stop(self):
        self._stop.set()

    def is_alive(self):
        return bool(self.thread and self.thread.is_alive())

    # ------------------------------------------------------------ jobs
    def _new_job(self, kind, clerk="", household_id=None, **extra):
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "clerk": clerk, "household_id": household_id,
               "stage": "queued", "status": "queued", "created_wall": self.clock(), "timings": {},
               "warnings": [], "error": None, "transcript": "", "asr": None, "llm": None, "seqs": [],
               "duration_s": None, "mode": None}
        job.update(extra)
        with self._jobs_lock:
            self.jobs[job["id"]] = job
            while len(self.jobs) > 200:
                self.jobs.popitem(last=False)
        return job

    def _enqueue(self, job, payload, prio=PRIO_INTAKE):
        self.q.put((prio, next(self._counter), job["id"], payload))
        return job["id"]

    def submit_audio(self, wav_bytes, clerk="", mock_text=None, household_id=None):
        job = self._new_job("audio", clerk, household_id)
        return self._enqueue(job, {"wav": wav_bytes, "mock_text": mock_text})

    def submit_text(self, text, clerk="", household_id=None):
        job = self._new_job("text", clerk, household_id)
        job["transcript"] = text or ""
        return self._enqueue(job, {"text": text or ""})

    def submit_refine(self, seq, clerk=""):
        job = self._new_job("refine", clerk, None, target_seq=int(seq))
        return self._enqueue(job, {"seq": int(seq)})

    def submit_warmup(self):
        job = self._new_job("warmup")
        return self._enqueue(job, {}, PRIO_WARMUP)

    @staticmethod
    def _snapshot(j):
        """Deep copy of a job the worker may be mutating (retry if a dict changes size mid-copy)."""
        for _ in range(10):
            try:
                snap = copy.deepcopy(j)
                snap.pop("_stage_t", None)
                return snap
            except RuntimeError:
                continue
        return {k: j.get(k) for k in ("id", "kind", "stage", "status", "seqs", "duration_s")}

    def get_job(self, job_id):
        with self._jobs_lock:
            j = self.jobs.get(job_id)
        return self._snapshot(j) if j else None

    def recent_jobs(self, n=8):
        with self._jobs_lock:
            jobs = [j for j in self.jobs.values() if j["kind"] != "warmup"][-n:]
        return [self._snapshot(j) for j in jobs][::-1]

    def backlog(self):
        """Queued intake jobs (not counting the one running)."""
        with self._jobs_lock:
            return sum(1 for j in self.jobs.values() if j["status"] == "queued" and j["kind"] in ("audio", "text"))

    # ------------------------------------------------------------ worker
    def _loop(self):
        while not self._stop.is_set():
            try:
                self.beat()
                try:
                    prio, _, job_id, payload = self.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                job = self.jobs.get(job_id)
                if job is None:
                    continue
                self.current_job = job_id
                try:
                    self._process(job, payload)
                except Exception as e:  # one bad job never kills the worker
                    job["status"] = "error"
                    job["stage"] = "error"
                    job["error"] = f"{type(e).__name__}: {e}"
                    self._log_error("worker_error", e)
                finally:
                    self.current_job = None
            except Exception as e:
                self._log_error("worker_error", e)

    def _log_error(self, etype, exc):
        now = time.monotonic()
        if now - self._last_error_log.get(etype, -1e9) < 60:
            return
        self._last_error_log[etype] = now
        try:
            self.store.log_event(etype, {"error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass

    def _stage(self, job, stage):
        now = time.monotonic()
        prev = job.get("_stage_t")
        if prev is not None and job["stage"] not in ("queued",):
            job["timings"][job["stage"]] = round(now - prev, 3)
        job["_stage_t"] = now
        job["stage"] = stage
        if stage not in ("done", "error"):
            job["status"] = "running"
        self.beat()

    def _finish(self, job, status="done"):
        self._stage(job, status)
        job["status"] = status
        job["timings"]["total"] = round(self.clock() - job["created_wall"], 3)
        job.pop("_stage_t", None)

    def _mode(self):
        return self.health.mode() if self.health else "FULL"

    def _fast(self):
        if self.health is not None and self.health.fast_condition():
            return True
        return self.backlog() >= int(self.cfg["pipeline"].get("backlog_fast_mode", 3))

    def _process(self, job, payload):
        kind = job["kind"]
        job["mode"] = self._mode()
        job["timings"]["queued"] = round(max(0.0, self.clock() - job["created_wall"]), 3)
        if kind == "warmup":
            self._process_warmup(job)
        elif kind == "audio":
            self._process_audio(job, payload)
        elif kind == "text":
            self._process_text(job, payload["text"], audio_seconds=0.0, flags=[])
        elif kind == "refine":
            self._process_refine(job, payload["seq"])

    def _process_warmup(self, job):
        self._stage(job, "refining")
        res = {"ok": False, "error": "no_llm"}
        if self.llm is not None:
            res = self.llm.warmup()
        job["llm"] = res
        if self.health is not None:
            self.health.on_warmup(res)
        self._finish(job)

    def _process_audio(self, job, payload):
        from . import audio  # lazy: numpy decode
        self._stage(job, "checking")
        try:
            x, sr = audio.decode_wav(payload["wav"])
        except Exception:
            job["warnings"].append(WARN["bad_audio"])
            return self._finish(job)
        info = audio.check_audio(x, max_seconds=float(self.cfg["asr"].get("max_seconds", 30)))
        x = info.pop("x", x)
        job["duration_s"] = round(float(info.get("duration_s") or 0.0), 2)
        job["audio_check"] = {k: v for k, v in info.items() if k != "x"}
        for w in info.get("warnings") or []:
            if w in WARN:
                job["warnings"].append(WARN[w])
        if not info.get("ok"):
            job["warnings"].append(WARN.get(info.get("reason"), WARN["silent"]))
            return self._finish(job)
        if self.asr is None:
            job["warnings"].append(WARN["asr_failed"])
            return self._finish(job)
        self._stage(job, "transcribing")
        fast = self._mode() == "FAST" or self._fast() or bool(getattr(self.health, "asr_fallback", False))
        try:
            if payload.get("mock_text") is not None or getattr(self.asr, "is_mock", False):
                res = self.asr.transcribe(x, fast=fast, mock_text=payload.get("mock_text"))
            else:
                res = self.asr.transcribe(x, fast=fast)
        except Exception as e:
            job["warnings"].append(WARN["asr_failed"])
            job["error"] = f"asr: {type(e).__name__}: {e}"
            if self.health is not None:
                self.health.on_asr_error(e)
            return self._finish(job)
        job["asr"] = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        text = (job["asr"].get("text") or "").strip()
        job["transcript"] = text
        if not text:
            job["warnings"].append(WARN["no_text"])
            return self._finish(job)
        flags = []
        if job["asr"].get("low_conf"):
            flags.append("asr_low_conf")
            job["warnings"].append(WARN["asr_low_conf"])
        if fast:
            flags.append("fast_mode")
        self._process_text(job, text, audio_seconds=float(info.get("duration_s") or 0.0), flags=flags,
                           already_fast=fast)

    def _need_as_ticket(self, need, now):
        return {"seq": None, "category": need.category, "item_code": need.item_code, "zone_code": need.zone_code,
                "location": need.location, "subject": need.subject, "summary": need.summary,
                "created_wall": now, "status": "draft", "quantity": need.quantity}

    def _process_text(self, job, text, audio_seconds=0.0, flags=None, already_fast=False):
        self._stage(job, "understanding")
        text = (text or "").strip()
        if not text:
            job["warnings"].append(WARN["empty"])
            return self._finish(job)
        now = self.clock()
        needs = extract.analyze(text, now)
        open_tickets = self.store.open_tickets()
        seq_by_index = {}
        for i, need in enumerate(needs):
            try:
                need.dup_candidates = dedupe.find_candidates(self._need_as_ticket(need, now), open_tickets, now)
            except Exception as e:
                self._log_error("dedupe_error", e)
                need.dup_candidates = []
            seq = self.store.create_draft(need, job_id=job["id"], transcript=text, audio_seconds=audio_seconds,
                                          flags=flags or [], household_id=job.get("household_id"))
            seq_by_index[i] = seq
            job["seqs"].append(seq)
        # LLM gate: FULL or TYPED, and no FAST condition
        mode = self._mode()
        if self.llm is None or mode not in ("FULL", "TYPED") or already_fast or self._fast():
            job["llm"] = {"skipped": True, "mode": mode}
            return self._finish(job)
        self._refine_with_llm(job, text, needs, seq_by_index, audio_seconds, flags)
        self._finish(job)

    def _refine_with_llm(self, job, text, needs, seq_by_index, audio_seconds=0.0, flags=None):
        self._stage(job, "refining")
        res = self.llm.extract(text)
        job["llm"] = {k: res.get(k) for k in ("error", "seconds", "tokens_per_s", "prompt_eval_count")}
        if self.health is not None:
            self.health.on_llm_result(res)
        if res.get("error"):
            flag = "llm_timeout" if res["error"] == "timeout" else "llm_error"
            job["warnings"].append(WARN[flag])
            for seq in seq_by_index.values():
                try:
                    self.store.apply_ai_fields(seq, {}, {}, [flag])
                except Conflict:
                    pass
            return
        now = self.clock()
        refined = extract.refine(needs, res.get("needs") or [], text, now)
        for need in refined:
            fields = {k: getattr(need, k) for k in AI_FIELDS}
            fields["missing_info"] = list(dict.fromkeys(list(need.ask_back or []) + list(need.missing_info or [])))
            ev = dict(need.evidence or {})
            if need.red_flags:
                ev["_red_flags"] = list(need.red_flags)
            if need.suppressed:
                ev["_suppressed"] = list(need.suppressed)
            fields["evidence"] = ev
            idx = need.rules_index
            if idx is not None and idx in seq_by_index:
                try:
                    self.store.apply_ai_fields(seq_by_index[idx], fields, dict(need.field_sources or {}),
                                               list(need.flags or []))
                except Conflict:
                    pass
            else:
                fl = list(need.flags or [])
                if "ai_only" not in fl:
                    fl.append("ai_only")
                need.flags = fl
                seq = self.store.create_draft(need, job_id=job["id"], transcript=text, audio_seconds=audio_seconds,
                                              flags=flags or [], household_id=job.get("household_id"))
                job["seqs"].append(seq)

    def _process_refine(self, job, seq):
        """Clerk asked for AI on an existing draft (FAST mode 'AI on request')."""
        t = self.store.get(seq)
        if t is None or not t.get("transcript"):
            job["warnings"].append(WARN["empty"])
            return self._finish(job)
        if self.llm is None:
            job["warnings"].append(WARN["llm_error"])
            return self._finish(job)
        text = t["transcript"]
        job["transcript"] = text
        now = self.clock()
        self._stage(job, "understanding")
        needs = extract.analyze(text, now)
        # map every rules need of this transcript to this ticket when it matches, else new drafts
        seq_by_index = {}
        for i, need in enumerate(needs):
            if (need.item_code and need.item_code == t.get("item_code")) or \
                    (not need.item_code and need.category == t.get("category")):
                seq_by_index[i] = seq
                break
        if not seq_by_index and needs:
            seq_by_index[0] = seq
        self._refine_with_llm(job, text, needs, seq_by_index)
        # needs without a ticket that the rules found: skip (they already exist from the first pass)
        self._finish(job)

    # ------------------------------------------------------------ check-in
    def checkin(self, household, clerk=""):
        """Record a household; every screening 'yes' becomes a draft. Returns (household_id, seqs)."""
        hid = self.store.checkin(household, clerk)
        h = self.store.household(hid) or {}
        screening = household.get("screening") or {}
        now = self.clock()
        seqs = []
        zone = int(h.get("zone_code") or 0)
        elderly = int(h.get("elderly") or 0)
        infants = int(h.get("infants") or 0)
        pregnant = int(h.get("pregnant") or 0)
        disabled = int(h.get("disabled") or 0)

        def draft(qkey, category, subject_group, item_code=0):
            need = extract.Need(category=category, item_code=item_code,
                                item=(codebook.item(item_code) or {}).get("zh", "") if item_code else "",
                                subject_group=subject_group, zone_code=zone,
                                location=codebook.zone_label(zone, h.get("location") or ""))
            need.priority, need.priority_reasons, need.emergency = triage.assign_priority(need, now)
            need.rule_priority = need.priority
            need.missing_info = triage.missing_info(need)
            need.ask_back = []
            need.summary = f"報到篩檢{qkey.upper()}：{codebook.cat(category)['zh'].split('（')[0]}"
            need.evidence = {"category": SCREENING_QUESTIONS[qkey]}
            need.field_sources = {"category": "screening"}
            seq = self.store.create_draft(need, job_id=f"checkin-{hid}", transcript=SCREENING_QUESTIONS[qkey],
                                          household_id=hid)
            seqs.append(seq)

        def yes(k):
            v = screening.get(k)
            return v is True or str(v).lower() in ("1", "true", "yes", "y", "是", "有")

        group_default = "elderly" if elderly > 0 else "unknown"
        if yes("q1"):
            draft("q1", 2, group_default)
        if yes("q2"):
            draft("q2", 3, group_default)
        if yes("q3"):
            made = False
            if disabled > 0:
                draft("q3", 5, "disabled")
                made = True
            if infants + pregnant > 0:
                draft("q3", 6, "infant" if infants > 0 else "pregnant")
                made = True
            if not made:
                draft("q3", 6, "unknown")
        if yes("q4"):
            draft("q4", 4, "unknown")
        if yes("q5"):
            draft("q5", 0, "unknown")
        return hid, seqs
