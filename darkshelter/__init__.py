"""DarkShelter: offline shelter intake desk, triage and thin-link sync (Stage I)."""
import sys as _sys

__version__ = "0.1.0"


def _safe_console():
    """Never crash on a zh-TW Windows console (cp950): symbols it lacks (✓ ⏳ 🚑) print as '?'."""
    for stream in (_sys.stdout, _sys.stderr):
        try:
            if stream is not None and (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(errors="replace")
        except Exception:
            pass


_safe_console()
