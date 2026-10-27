"""Audio intake checks (SPEC 6.2): WAV decode + silence / length / clipping checks.

decode_wav accepts RIFF/WAVE PCM at any sample rate and channel count (16-bit is the
browser recorder's format; 8/24/32-bit PCM and 32-bit float are accepted too) and returns
float32 mono at 16 kHz using linear-interpolation resampling.
"""
import math
import struct

import numpy as np

TARGET_SR = 16000
MIN_SECONDS = 0.6
SILENT_DBFS = -50.0
CLIP_LEVEL = 0.99
CLIP_RATIO = 0.02
DBFS_FLOOR = -120.0

# zh / en texts for check results (shown at the desk)
REASON_TEXT = {
    "too_short": ("錄音太短，請再錄一次", "Recording too short"),
    "silent": ("沒有聽到聲音", "No speech heard"),
    "clipped": ("音量過大（爆音），請離麥克風遠一點", "Audio is clipped; move back from the mic"),
    "too_long": ("錄音超過上限，只處理前段", "Recording too long; only the first part is used"),
}


class AudioError(Exception):
    """The upload is not a WAV file we can decode."""


_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_FLOAT = 3
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _parse_riff(data):
    """Return (format_tag, channels, sample_rate, bits, pcm_bytes)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise AudioError("audio must be bytes")
    data = bytes(data)
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("not a RIFF/WAVE file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise AudioError("bad fmt chunk")
            tag, nch, sr, _byte_rate, _align, bits = struct.unpack_from("<HHIIHH", data, body)
            if tag == _WAVE_FORMAT_EXTENSIBLE and size >= 40 and body + 26 <= len(data):
                tag = struct.unpack_from("<H", data, body + 24)[0]
            fmt = (tag, nch, sr, bits)
        elif cid == b"data":
            # 0 / 0xFFFFFFFF = streaming writers that never patched the size: take the rest
            end = len(data) if size in (0, 0xFFFFFFFF) else min(len(data), body + size)
            pcm = data[body:end]
            if fmt is not None:
                break
        pos = body + size + (size & 1)
    if fmt is None:
        raise AudioError("WAV has no fmt chunk")
    if pcm is None:
        raise AudioError("WAV has no data chunk")
    return fmt + (pcm,)


def _to_float(pcm, tag, bits, nch):
    if nch < 1 or nch > 32:
        raise AudioError(f"unsupported channel count {nch}")
    if tag == _WAVE_FORMAT_PCM and bits == 16:
        width = 2
        frame = width * nch
        a = np.frombuffer(pcm[:len(pcm) - len(pcm) % frame], dtype="<i2").astype(np.float32) / 32768.0
    elif tag == _WAVE_FORMAT_PCM and bits == 8:
        frame = nch
        a = (np.frombuffer(pcm[:len(pcm) - len(pcm) % frame], dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == _WAVE_FORMAT_PCM and bits == 24:
        frame = 3 * nch
        raw = np.frombuffer(pcm[:len(pcm) - len(pcm) % frame], dtype=np.uint8).reshape(-1, 3)
        ints = (raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16))
        ints = np.where(ints & 0x800000, ints - (1 << 24), ints)
        a = ints.astype(np.float32) / float(1 << 23)
    elif tag == _WAVE_FORMAT_PCM and bits == 32:
        frame = 4 * nch
        a = np.frombuffer(pcm[:len(pcm) - len(pcm) % frame], dtype="<i4").astype(np.float32) / float(1 << 31)
    elif tag == _WAVE_FORMAT_FLOAT and bits == 32:
        frame = 4 * nch
        a = np.frombuffer(pcm[:len(pcm) - len(pcm) % frame], dtype="<f4").astype(np.float32)
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    else:
        raise AudioError(f"unsupported WAV encoding (format {tag}, {bits}-bit); use 16-bit PCM")
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a.astype(np.float32)


def resample_linear(x, sr, target=TARGET_SR):
    """Linear-interpolation resample to `target` Hz."""
    if sr == target or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * float(target) / float(sr)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n_out, dtype=np.float64) * (float(sr) / float(target))
    return np.interp(t, np.arange(len(x), dtype=np.float64), x.astype(np.float64)).astype(np.float32)


def decode_wav(data):
    """WAV bytes -> (float32 mono @16 kHz in [-1, 1], original sample rate)."""
    tag, nch, sr, bits, pcm = _parse_riff(data)
    if sr <= 0 or sr > 768000:
        raise AudioError(f"bad sample rate {sr}")
    x = _to_float(pcm, tag, bits, nch)
    return resample_linear(x, sr), int(sr)


def rms_dbfs(x):
    if len(x) == 0:
        return DBFS_FLOOR
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    if rms <= 0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20.0 * math.log10(rms))


def check_audio(x, max_seconds=30):
    """Quality checks on 16 kHz mono audio.

    Returns {ok, reason, warnings, duration_s, rms_dbfs, clip_ratio, x}:
    reason 'too_short' (< 0.6 s) or 'silent' (RMS < -50 dBFS) -> ok False;
    warning 'clipped' if > 2% of samples have |x| >= 0.99;
    warning 'too_long' if longer than max_seconds (x is truncated; duration_s stays the original).
    """
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    duration = len(x) / float(TARGET_SR)
    level = rms_dbfs(x)
    clip_ratio = float(np.mean(np.abs(x) >= CLIP_LEVEL)) if len(x) else 0.0
    warnings = []
    reason = None
    if duration < MIN_SECONDS:
        reason = "too_short"
    elif level < SILENT_DBFS:
        reason = "silent"
    if clip_ratio > CLIP_RATIO:
        warnings.append("clipped")
    max_n = int(float(max_seconds) * TARGET_SR) if max_seconds else 0
    if max_n and len(x) > max_n:
        warnings.append("too_long")
        x = x[:max_n]
    return {
        "ok": reason is None,
        "reason": reason,
        "warnings": warnings,
        "duration_s": round(duration, 3),
        "rms_dbfs": round(level, 1),
        "clip_ratio": round(clip_ratio, 4),
        "x": x,
    }


def public(check):
    """check_audio result without the array (safe for JSON)."""
    return {k: v for k, v in check.items() if k != "x"}
