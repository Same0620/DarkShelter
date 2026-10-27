"""LLM extraction (SPEC 6.4 / 7): Ollama adapter over urllib, schema validation, MockLLM.

The LLM only fills in what was said; urgency comes from the rule table. The output is
schema-locked (Ollama `format`), validated here, and merged by extract.merge_llm.
No executor and no threads: the socket timeout bounds the whole non-streaming call, and
closing the socket aborts generation on the Ollama side.
"""
import json
import socket
import time
import urllib.error
import urllib.request

from .config import ROOT

CATEGORY_KEYS = ["MEDICAL_EMERGENCY", "LIFE_SUSTAINING", "MEDICATION", "PERSON_AT_RISK", "MOBILITY",
                 "INFANT_CHILD", "FOOD_WATER", "SUPPLIES", "MENTAL_HEALTH", "COMMUNICATION", "PET",
                 "INFRASTRUCTURE", "PROTECTION", "OTHER"]
SUBJECT_GROUPS = ["infant", "child", "adult", "elderly", "pregnant", "disabled", "unknown"]
STRING_FIELDS = ("item", "subject", "location", "time_phrase", "summary")
MAX_NEEDS = 5
MAX_CHARS = 40
MAX_QUANTITY = 10000

SCHEMA = {
    "type": "object",
    "required": ["needs"],
    "properties": {
        "needs": {
            "type": "array",
            "maxItems": MAX_NEEDS,
            "items": {
                "type": "object",
                "required": ["category", "item", "quantity", "subject", "subject_group", "location",
                             "time_phrase", "summary"],
                "properties": {
                    "category": {"type": "string", "enum": CATEGORY_KEYS},
                    "item": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "subject": {"type": "string"},
                    "subject_group": {"type": "string", "enum": SUBJECT_GROUPS},
                    "location": {"type": "string"},
                    "time_phrase": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        }
    },
}

PROMPT_DIR = ROOT / "data" / "prompts"
USER_PREFIX = "逐字稿："


def load_system_prompt():
    return (PROMPT_DIR / "extract_system.txt").read_text(encoding="utf-8").strip()


def load_fewshot():
    """The two few-shot examples: [{transcript, output}]."""
    return json.loads((PROMPT_DIR / "fewshot.json").read_text(encoding="utf-8"))


def fewshot_messages():
    msgs = []
    for ex in load_fewshot():
        msgs.append({"role": "user", "content": USER_PREFIX + ex["transcript"]})
        msgs.append({"role": "assistant",
                     "content": json.dumps(ex["output"], ensure_ascii=False, separators=(",", ":"))})
    return msgs


def _clean_str(v):
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    s = "".join(ch for ch in s if ch >= " ")
    s = s[:MAX_CHARS].strip()
    return s or None


def _clean_quantity(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        q = int(float(v))
    except (TypeError, ValueError):
        return None
    q = max(0, min(MAX_QUANTITY, q))
    return q or None


def validate(obj):
    """LLM output -> list of clean need dicts.

    ≤ 5 needs; needs whose category is not in the enum are dropped; strings are truncated
    to 40 characters; quantity is clamped to 0-10000; empty strings and quantity 0 become None
    ("not said"). A wrong top-level shape raises ValueError.
    """
    if isinstance(obj, (bytes, bytearray)):
        obj = obj.decode("utf-8")
    if isinstance(obj, str):
        obj = json.loads(obj)  # ValueError on bad JSON
    if not isinstance(obj, dict) or not isinstance(obj.get("needs"), list):
        raise ValueError("LLM output must be an object with a 'needs' list")
    out = []
    for n in obj["needs"]:
        if len(out) >= MAX_NEEDS:
            break
        if not isinstance(n, dict):
            continue
        cat = str(n.get("category") or "").strip().upper()
        if cat not in CATEGORY_KEYS:
            continue
        sg = str(n.get("subject_group") or "").strip().lower()
        need = {"category": cat, "subject_group": sg if sg in SUBJECT_GROUPS else "unknown",
                "quantity": _clean_quantity(n.get("quantity"))}
        for k in STRING_FIELDS:
            need[k] = _clean_str(n.get(k))
        out.append(need)
    return out


def _is_timeout(exc):
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)):
        return True
    return False


class OllamaLLM:
    """Qwen2.5-1.5B (or any model) through Ollama's /api/chat with a JSON-schema format."""

    def __init__(self, url="http://127.0.0.1:11434", model="qwen2.5:1.5b", timeout_s=15, num_predict=320,
                 keep_alive=-1, warmup_timeout_s=60, num_ctx=2048, use_mmap=True):
        self.url = str(url).rstrip("/")
        self.model = model
        self.timeout_s = float(timeout_s)
        self.num_predict = int(num_predict)
        self.keep_alive = keep_alive
        self.warmup_timeout_s = float(warmup_timeout_s)
        self.num_ctx = int(num_ctx)
        # Memory-map the model file: ~340 MB less committed memory on Windows, which lets Whisper
        # and the LLM fit together on a laptop that is short of memory. Sent on every request so
        # Ollama never reloads the model because options differ.
        self.use_mmap = bool(use_mmap)
        self.system_prompt = load_system_prompt()
        self.fewshot = fewshot_messages()
        # The LLM is local by design: never route it through a system proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._avail_at = None
        self._avail = False
        self.last_tokens_per_s = None
        self.prefix_cached = None

    @classmethod
    def from_config(cls, cfg):
        c = cfg.get("llm", {})
        return cls(url=c.get("url", "http://127.0.0.1:11434"), model=c.get("model", "qwen2.5:1.5b"),
                   timeout_s=c.get("timeout_s", 15), num_predict=c.get("num_predict", 320),
                   keep_alive=c.get("keep_alive", -1), warmup_timeout_s=c.get("warmup_timeout_s", 60),
                   use_mmap=c.get("use_mmap", True))

    # ------------------------------------------------------------ availability
    def tags(self, timeout=2.0):
        with self._opener.open(self.url + "/api/tags", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def available(self, force=False):
        """GET /api/tags (2 s timeout), cached 10 s; True only if the model is listed."""
        now = time.monotonic()
        if not force and self._avail_at is not None and now - self._avail_at < 10.0:
            return self._avail
        ok = False
        try:
            data = self.tags(timeout=2.0)
            names = set()
            for m in data.get("models", []) or []:
                for k in ("name", "model"):
                    if m.get(k):
                        names.add(m[k])
            ok = self.model in names or (":" not in self.model and f"{self.model}:latest" in names)
        except Exception:
            ok = False
        self._avail_at, self._avail = time.monotonic(), ok
        return ok

    # ------------------------------------------------------------ extraction
    def build_body(self, transcript):
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system_prompt}, *self.fewshot,
                         {"role": "user", "content": USER_PREFIX + (transcript or "")}],
            "format": SCHEMA,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_predict": self.num_predict, "num_ctx": self.num_ctx,
                        "use_mmap": self.use_mmap},
        }

    def extract(self, transcript, timeout_s=None):
        """-> {needs, error, seconds, tokens_per_s, prompt_eval_count, raw}.

        error: None | 'timeout' | 'llm_error' | 'invalid_json'.
        """
        t0 = time.monotonic()
        deadline = t0 + float(self.timeout_s if timeout_s is None else timeout_s)
        payload = json.dumps(self.build_body(transcript), ensure_ascii=False).encode("utf-8")
        res = {"needs": [], "error": None, "seconds": 0.0, "tokens_per_s": None, "prompt_eval_count": 0,
               "raw": None}
        attempts = 0
        while True:
            attempts += 1
            req = urllib.request.Request(self.url + "/api/chat", data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with self._opener.open(req, timeout=max(1.0, deadline - time.monotonic())) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001 — any failure becomes a flag, never a crash
                res["error"] = "timeout" if _is_timeout(e) else "llm_error"
                res["detail"] = repr(e)[:200]
                break
            eval_count = data.get("eval_count", 0) or 0
            eval_duration = data.get("eval_duration", 0) or 0
            res["prompt_eval_count"] = data.get("prompt_eval_count", 0) or 0
            res["prompt_eval_s"] = round((data.get("prompt_eval_duration", 0) or 0) / 1e9, 4)
            if eval_duration > 0:
                res["tokens_per_s"] = round(eval_count / (eval_duration / 1e9), 2)
                self.last_tokens_per_s = res["tokens_per_s"]
            content = (data.get("message") or {}).get("content", "")
            res["raw"] = content
            try:
                res["needs"] = validate(json.loads(content))
                res["error"] = None
                break
            except (ValueError, TypeError):
                if attempts < 2 and deadline - time.monotonic() > 0:
                    continue  # retry once if time remains
                res["error"] = "invalid_json"
                break
        res["attempts"] = attempts
        res["seconds"] = round(time.monotonic() - t0, 3)
        return res

    def warmup(self, timeout_s=None):
        """extract('測試') twice with warmup_timeout_s (or `timeout_s`: the short probe after timeouts).

        prefix_cached: the 2nd prompt_eval_count is < half the 1st (SPEC). Ollama >= 0.40
        (llama-server runner) reports the full prompt length even when the prefix is reused,
        so a 2nd prompt_eval_duration < half the 1st also counts as cached.
        """
        t0 = time.monotonic()
        limit = self.warmup_timeout_s if timeout_s is None else float(timeout_s)
        r1 = self.extract("測試", timeout_s=limit)
        if r1["error"] in ("timeout", "llm_error"):
            return {"ok": False, "prefix_cached": False, "tokens_per_s": None,
                    "seconds": round(time.monotonic() - t0, 3), "error": r1["error"],
                    "prompt_eval_count": [r1["prompt_eval_count"]]}
        r2 = self.extract("測試", timeout_s=limit)
        p1, p2 = r1["prompt_eval_count"], r2["prompt_eval_count"]
        d1, d2 = r1.get("prompt_eval_s") or 0.0, r2.get("prompt_eval_s") or 0.0
        by_count = bool(p1) and p2 < p1 / 2.0
        by_time = d1 > 0 and d2 < d1 / 2.0
        cached = (by_count or by_time) and r2["error"] not in ("timeout", "llm_error")
        tps = r2["tokens_per_s"] or r1["tokens_per_s"]
        self.prefix_cached = cached
        if tps:
            self.last_tokens_per_s = tps
        ok = r1["error"] is None or r2["error"] is None
        return {"ok": ok, "prefix_cached": cached, "tokens_per_s": tps, "seconds": round(time.monotonic() - t0, 3),
                "error": None if ok else (r2["error"] or r1["error"]), "prompt_eval_count": [p1, p2],
                "prompt_eval_s": [d1, d2]}


# ---------------------------------------------------------------------- MockLLM

def _field(need, key, default=None):
    if isinstance(need, dict):
        return need.get(key, default)
    return getattr(need, key, default)


def need_to_llm_dict(need):
    """A rules Need (dataclass or dict, category = int code) -> the LLM JSON need."""
    from . import codebook
    cat = _field(need, "category", 0)
    if isinstance(cat, str) and cat.strip().upper() in CATEGORY_KEYS:
        key = cat.strip().upper()
    else:
        try:
            key = codebook.cat(int(cat or 0))["key"]
        except (TypeError, ValueError):
            key = "OTHER"
    item = _field(need, "item", "") or ""
    subject = _field(need, "subject", "") or ""
    summary = _field(need, "summary", "") or (subject + item)[:20]
    sg = _field(need, "subject_group", "unknown") or "unknown"
    return {"category": key, "item": item, "quantity": int(_field(need, "quantity", 0) or 0),
            "subject": subject, "subject_group": sg if sg in SUBJECT_GROUPS else "unknown",
            "location": _field(need, "location", "") or "", "time_phrase": _field(need, "time_phrase", "") or "",
            "summary": summary}


def _inject_dict(inject):
    """A dict in LLM format, or a keyword like '胸痛' looked up in the codebook items."""
    if isinstance(inject, dict):
        d = {"category": "OTHER", "item": "", "quantity": 0, "subject": "", "subject_group": "unknown",
             "location": "", "time_phrase": "", "summary": ""}
        d.update(inject)
        return d
    from . import codebook
    word = str(inject)
    key = "OTHER"
    for it in codebook.items():
        if word in it["zh"]:
            key = codebook.cat(it["category"])["key"]
            break
    return {"category": key, "item": word, "quantity": 0, "subject": "", "subject_group": "unknown",
            "location": "", "time_phrase": "", "summary": word}


class MockLLM:
    """Converts rules_extract output into the LLM JSON after a delay (default 0.5 s).

    drop_index: omit one need (tests the red-flag coverage invariant).
    inject: add a need (dict in LLM format or a keyword such as '胸痛').
    error: force 'timeout' | 'llm_error' | 'invalid_json'.
    """

    model = "mock"

    def __init__(self, delay=0.5, drop_index=None, inject=None, error=None):
        self.delay = float(delay or 0.0)
        self.drop_index = drop_index
        self.inject = inject
        self.error = error
        self.calls = 0
        self.last_tokens_per_s = 99.0
        self.prefix_cached = True

    def available(self, force=False):
        return True

    def warmup(self, timeout_s=None):
        return {"ok": True, "prefix_cached": True, "tokens_per_s": 99.0, "seconds": 0.0, "error": None,
                "prompt_eval_count": [0, 0]}

    def extract(self, transcript, timeout_s=None):
        t0 = time.monotonic()
        self.calls += 1
        timeout = None if timeout_s is None else float(timeout_s)
        if self.delay:
            if timeout is not None and self.delay > timeout:
                time.sleep(timeout)
                return {"needs": [], "error": "timeout", "seconds": round(time.monotonic() - t0, 3),
                        "tokens_per_s": None, "prompt_eval_count": 0, "raw": None}
            time.sleep(self.delay)
        if self.error:
            return {"needs": [], "error": self.error, "seconds": round(time.monotonic() - t0, 3),
                    "tokens_per_s": None, "prompt_eval_count": 0, "raw": None}
        from . import extract as ex  # lazy: extract imports nothing from here
        dicts = [need_to_llm_dict(n) for n in ex.rules_extract(transcript or "", time.time())]
        if self.drop_index is not None and 0 <= int(self.drop_index) < len(dicts):
            del dicts[int(self.drop_index)]
        if self.inject is not None:
            dicts.append(_inject_dict(self.inject))
        raw = json.dumps({"needs": dicts}, ensure_ascii=False)
        return {"needs": validate({"needs": dicts}), "error": None, "seconds": round(time.monotonic() - t0, 3),
                "tokens_per_s": 99.0, "prompt_eval_count": 0, "raw": raw}
