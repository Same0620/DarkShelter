"""Synthetic WAVs for the audio checks (SPEC 11), plus optional Mandarin TTS clips.

Functions return WAV bytes (16-bit PCM); the CLI writes them to a directory:
  silence.wav        2 s of digital silence             -> rejected 'silent'
  short_0.3s.wav     0.3 s tone                           -> rejected 'too_short'
  clipped.wav        2 s sine driven into clipping        -> warned 'clipped'
  long_40s.wav       40 s speech-like signal              -> warned 'too_long' (truncated to 30 s)
  stereo_48k.wav     2 s speech-like, 48 kHz stereo        -> resampled to 16 kHz mono
  speech_like.wav    2 s harmonic, amplitude-modulated tone -> passes the checks

tts_wav(text) synthesises real Mandarin speech with the OS voice when one exists
(Windows SAPI zh-TW voice, or macOS `say`); it returns None elsewhere.

Usage: python scripts/make_test_wavs.py --out eval/results/test_wavs
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 16000


def wav_bytes(x, sr=SR):
    """float array (n,) or (n, channels) in [-1, 1] -> 16-bit PCM WAV bytes."""
    x = np.asarray(x, dtype=np.float64)
    channels = 1 if x.ndim == 1 else x.shape[1]
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _t(seconds, sr=SR):
    return np.arange(int(round(seconds * sr))) / float(sr)


def speech_like_signal(seconds=2.0, sr=SR, seed=0, level=0.25):
    """Voice-ish: 140 Hz harmonics, syllable-rate amplitude modulation, a little noise (seeded)."""
    rng = np.random.default_rng(seed)
    t = _t(seconds, sr)
    f0 = 140.0 * (1.0 + 0.05 * np.sin(2 * np.pi * 0.7 * t))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    x = sum((1.0 / k) * np.sin(k * phase) for k in range(1, 8))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t) ** 2
    x = x * env + 0.02 * rng.standard_normal(len(t))
    return level * x / (np.max(np.abs(x)) or 1.0)


def silence(seconds=2.0, sr=SR):
    return wav_bytes(np.zeros(int(seconds * sr)), sr)


def short_tone(seconds=0.3, sr=SR):
    return wav_bytes(0.3 * np.sin(2 * np.pi * 440.0 * _t(seconds, sr)), sr)


def clipped_sine(seconds=2.0, sr=SR):
    return wav_bytes(np.clip(1.6 * np.sin(2 * np.pi * 300.0 * _t(seconds, sr)), -1.0, 1.0), sr)


def long_signal(seconds=40.0, sr=SR):
    return wav_bytes(speech_like_signal(seconds, sr, seed=1), sr)


def stereo_48k(seconds=2.0):
    left = speech_like_signal(seconds, 48000, seed=2)
    right = speech_like_signal(seconds, 48000, seed=3)
    return wav_bytes(np.stack([left, right], axis=1), 48000)


def speech_like(seconds=2.0, sr=SR):
    return wav_bytes(speech_like_signal(seconds, sr, seed=0), sr)


def quiet_tone(seconds=2.0, sr=SR, dbfs=-60.0):
    amp = 10 ** (dbfs / 20.0) * np.sqrt(2.0)
    return wav_bytes(amp * np.sin(2 * np.pi * 440.0 * _t(seconds, sr)), sr)


def all_wavs():
    return {
        "silence.wav": silence(),
        "short_0.3s.wav": short_tone(),
        "clipped.wav": clipped_sine(),
        "long_40s.wav": long_signal(),
        "stereo_48k.wav": stereo_48k(),
        "speech_like.wav": speech_like(),
        "quiet_-60dBFS.wav": quiet_tone(),
    }


# ---------------------------------------------------------------- TTS (optional)

_PS_TTS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath $args[0] -Encoding UTF8 -Raw
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1
if ($null -eq $voice) { exit 3 }
$s.SelectVoice($voice.VoiceInfo.Name)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($args[1], $fmt)
$s.Speak($text)
$s.Dispose()
"""


def tts_available():
    if sys.platform.startswith("win"):
        return shutil.which("powershell") is not None
    if sys.platform == "darwin":
        return shutil.which("say") is not None
    return False


def tts_wav(text, timeout=60):
    """Mandarin speech for `text` as 16 kHz mono WAV bytes, or None if no OS voice is available."""
    if not tts_available():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="ds_tts_"))
    try:
        out = tmp / "tts.wav"
        if sys.platform.startswith("win"):
            txt = tmp / "text.txt"
            txt.write_text(text, encoding="utf-8")
            ps1 = tmp / "tts.ps1"
            ps1.write_text(_PS_TTS, encoding="utf-8-sig")
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                                str(txt), str(out)], capture_output=True, timeout=timeout)
        else:
            r = subprocess.run(["say", "-v", "Mei-Jia", "-o", str(out), "--file-format=WAVE",
                                "--data-format=LEI16@16000", text], capture_output=True, timeout=timeout)
            if r.returncode != 0:
                r = subprocess.run(["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000",
                                    text], capture_output=True, timeout=timeout)
        if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
            return None
        return out.read_bytes()
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write synthetic test WAVs")
    ap.add_argument("--out", default=os.path.join("eval", "results", "test_wavs"))
    ap.add_argument("--tts", default=None, help="also synthesise this Mandarin sentence (OS voice)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in all_wavs().items():
        (out / name).write_bytes(data)
        print(f"wrote {out / name} ({len(data)} bytes)")
    if a.tts:
        data = tts_wav(a.tts)
        if data:
            (out / "tts.wav").write_bytes(data)
            print(f"wrote {out / 'tts.wav'} ({len(data)} bytes)")
        else:
            print("no Mandarin TTS voice available on this machine")


if __name__ == "__main__":
    main()
