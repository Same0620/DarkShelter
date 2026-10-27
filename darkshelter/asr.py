"""Speech-to-text (SPEC 6.3): faster-whisper adapter, phantom filter, MockASR.

faster_whisper is imported lazily inside try/except Exception: ctranslate2 without the
Visual C++ runtime raises OSError, not ImportError. A failure means TYPED mode.
"""
import gc
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field

from .config import resolve_path
from .textnorm import to_traditional

DOMAIN_PROMPT = ("以下是台灣避難收容處所登記台的對話，使用繁體中文。常見詞：洗腎、胰島素、氧氣、製氧機、輪椅、"
                 "尿布、奶粉、慢性處方箋、高血壓、糖尿病、阿嬤、阿公、受困、失聯、毛毯、飲用水、床位。")

# PLAN §10 residents, a duplicate dialysis report, and a negated symptom.
DEMO_SENTENCES = [
    "阿嬤明天早上要洗腎，她的胰島素今天也用完了",
    "阿公的降血壓藥只剩兩天份，他在B區5床",
    "我爸還在中正路三巷的家裡沒出來",
    "我是她孫女，阿嬤明天早上要洗腎",
    "我沒有胸痛，只是有點累",
]

# Whisper's known hallucinations on silence/noise, in Traditional and Simplified forms.
PHANTOMS = [
    "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "請不吝點讚訂閱轉發打賞支持明鏡與點點欄目",
    "明鏡與點點", "明镜与点点",
    "謝謝觀看", "谢谢观看", "感謝觀看", "感谢观看",
    "字幕由Amara.org社群提供", "字幕由Amara.org社区提供",
    "字幕志願者", "字幕志愿者",
    "優優獨播劇場", "优优独播剧场",
    "請訂閱", "请订阅", "點贊", "點讚", "点赞", "中文字幕",
]
_PHANTOM_RE = re.compile("|".join(re.escape(p) for p in sorted(PHANTOMS, key=len, reverse=True)), re.IGNORECASE)
_MEANINGFUL = re.compile(r"[0-9A-Za-z㐀-䶿一-鿿豈-﫿]")
_LEADING_PUNCT = re.compile(r"^[\s，。、；：！？,.;:!?…·\-—]+")
_REPEAT_PUNCT = re.compile(r"([，。、；：！？,.;:!?])[\s，。、；：！？,.;:!?]+")

# low-confidence thresholds (SPEC 6.3)
LOW_AVG_LOGPROB = -1.0
HIGH_NO_SPEECH = 0.6
HIGH_COMPRESSION = 2.4
MIN_CHARS_PER_S = 0.8


class AsrUnavailable(RuntimeError):
    """faster-whisper could not be imported or the model could not be loaded."""


@dataclass
class AsrResult:
    text: str = ""
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 0.0
    language: str = "zh"
    low_conf: bool = False
    reasons: list = field(default_factory=list)
    seconds: float = 0.0
    model: str = ""
    raw_text: str = ""
    speech_seconds: float = 0.0

    def to_dict(self):
        return asdict(self)


def meaningful_chars(text):
    return len(_MEANINGFUL.findall(text or ""))


def filter_phantoms(text):
    """Remove known phantom phrases (both scripts). < 2 meaningful characters left -> ''."""
    if not text:
        return ""
    t = _PHANTOM_RE.sub("", text)
    t = _REPEAT_PUNCT.sub(r"\1", t)
    t = _LEADING_PUNCT.sub("", t).strip()
    if meaningful_chars(t) < 2:
        return ""
    return t


def _join_segment_texts(texts):
    out = ""
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if out and out[-1].isascii() and out[-1].isalnum() and t[0].isascii() and t[0].isalnum():
            out += " "
        out += t
    return out


def summarize_segments(segments, seconds=0.0, model="", language="zh"):
    """Materialised Whisper segments -> AsrResult (duration-weighted logprob, max NSP/CR)."""
    texts, total_w, wsum, max_nsp, max_cr, speech = [], 0.0, 0.0, 0.0, 0.0, 0.0
    for s in segments:
        start, end = float(getattr(s, "start", 0.0) or 0.0), float(getattr(s, "end", 0.0) or 0.0)
        dur = max(0.0, end - start)
        speech += dur
        w = dur if dur > 0 else 1e-3
        wsum += float(getattr(s, "avg_logprob", 0.0) or 0.0) * w
        total_w += w
        max_nsp = max(max_nsp, float(getattr(s, "no_speech_prob", 0.0) or 0.0))
        max_cr = max(max_cr, float(getattr(s, "compression_ratio", 0.0) or 0.0))
        texts.append(getattr(s, "text", ""))
    raw = _join_segment_texts(texts)
    text = filter_phantoms(raw)
    text = filter_phantoms(to_traditional(text)) if text else ""
    avg_lp = (wsum / total_w) if total_w > 0 else 0.0
    reasons = []
    if not text:
        reasons.append("no_speech")
    else:
        if avg_lp < LOW_AVG_LOGPROB:
            reasons.append("avg_logprob")
        if max_nsp > HIGH_NO_SPEECH:
            reasons.append("no_speech_prob")
        if max_cr > HIGH_COMPRESSION:
            reasons.append("compression_ratio")
        if speech > 0 and meaningful_chars(text) / speech < MIN_CHARS_PER_S:
            reasons.append("chars_per_s")
    return AsrResult(text=text, avg_logprob=round(avg_lp, 4), no_speech_prob=round(max_nsp, 4),
                     compression_ratio=round(max_cr, 4), language=language or "zh", low_conf=bool(reasons),
                     reasons=reasons, seconds=round(float(seconds), 3), model=model, raw_text=raw,
                     speech_seconds=round(speech, 3))


def _asr_cfg(cfg):
    return (cfg or {}).get("asr", {}) or {}


def _is_memory_error(e):
    msg = repr(e).lower()
    return isinstance(e, MemoryError) or "malloc" in msg or "memory" in msg


def load_whisper(name, cfg, local_files_only=True, memory_retries=1, retry_delay_s=3.0):
    """The one loader: WhisperModel(name, device='cpu', compute_type, cpu_threads, download_root,
    local_files_only). Runtime and selftest pass True; download_models.py passes False.

    An allocation failure (e.g. ctranslate2's `mkl_malloc: failed to allocate memory` when the
    laptop is short of memory) is retried once after gc + a short pause before giving up.
    """
    try:
        from faster_whisper import WhisperModel  # lazy: optional heavy dependency
    except Exception as e:  # OSError when the VC++ runtime is missing
        raise AsrUnavailable(f"faster-whisper import failed: {e!r}") from e
    a = _asr_cfg(cfg)
    threads = int(a.get("cpu_threads") or 0) or (os.cpu_count() or 1)
    models_dir = resolve_path(((cfg or {}).get("paths") or {}).get("models_dir"))
    attempt = 0
    while True:
        try:
            return WhisperModel(name, device="cpu", compute_type=a.get("compute_type", "int8"), cpu_threads=threads,
                                download_root=str(models_dir) if models_dir else None,
                                local_files_only=local_files_only)
        except Exception as e:
            if _is_memory_error(e) and attempt < memory_retries:
                attempt += 1
                gc.collect()
                time.sleep(retry_delay_s)
                continue
            if _is_memory_error(e):
                hint = "not enough free memory: close browser tabs or other apps and retry"
            elif local_files_only:
                hint = "run scripts/download_models.py while online"
            else:
                hint = "check the network"
            raise AsrUnavailable(f"cannot load Whisper '{name}': {e!r} ({hint})") from e


class FasterWhisperASR:
    """Two lazily loaded models: asr.model (normal) and asr.fast_model (FAST mode)."""

    def __init__(self, cfg):
        self.cfg = cfg or {}
        a = _asr_cfg(self.cfg)
        self.model_name = a.get("model", "small")
        self.fast_model_name = a.get("fast_model", "base")
        self.language = a.get("language", "zh")
        self.beam_size = int(a.get("beam_size", 1))
        self._models = {}
        self._lock = threading.Lock()

    def name_for(self, fast=False):
        return self.fast_model_name if fast else self.model_name

    def load(self, fast=False):
        name = self.name_for(fast)
        with self._lock:
            m = self._models.get(name)
            if m is None:
                m = load_whisper(name, self.cfg, local_files_only=True)
                self._models[name] = m
        return m

    def loaded(self):
        return sorted(self._models)

    def transcribe(self, x, fast=False, mock_text=None):
        t0 = time.monotonic()
        name = self.name_for(fast)
        model = self.load(fast)
        segments, info = model.transcribe(
            x, language=self.language, beam_size=self.beam_size, temperature=0.0, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, initial_prompt=DOMAIN_PROMPT,
            condition_on_previous_text=False, no_speech_threshold=0.6)
        segs = list(segments)  # materialise: decoding happens lazily while iterating
        lang = getattr(info, "language", None) or self.language
        return summarize_segments(segs, time.monotonic() - t0, name, lang)


class MockASR:
    """Returns the request's mock_text; otherwise cycles through DEMO_SENTENCES."""

    def __init__(self, cfg=None, sentences=None):
        self.cfg = cfg or {}
        self.model_name = "mock"
        self.fast_model_name = "mock"
        self.sentences = list(sentences or DEMO_SENTENCES)
        self._i = 0
        self._lock = threading.Lock()

    def name_for(self, fast=False):
        return "mock"

    def load(self, fast=False):
        return self

    def loaded(self):
        return ["mock"]

    def reset(self):
        with self._lock:
            self._i = 0

    def transcribe(self, x=None, fast=False, mock_text=None):
        t0 = time.monotonic()
        if mock_text is None:
            with self._lock:
                mock_text = self.sentences[self._i % len(self.sentences)]
                self._i += 1
        text = filter_phantoms(to_traditional(mock_text))
        speech = (len(x) / 16000.0) if x is not None and hasattr(x, "__len__") else 0.0
        return AsrResult(text=text, avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.2, language="zh",
                         low_conf=not text, reasons=[] if text else ["no_speech"],
                         seconds=round(time.monotonic() - t0, 3), model="mock", raw_text=mock_text,
                         speech_seconds=round(speech, 3))
