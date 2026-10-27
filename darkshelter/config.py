"""Configuration: the user's JSON file merged over DEFAULTS.

Relative paths inside the config resolve against the repo root (never the
current directory). The HMAC key is generated on first run and written back
into the config file so the desk, the EOC and the emulator share it.
"""
import copy
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "desk_id": 7,
    "desk_letter": "B",
    "shelter_name": "示範國小 避難收容處所",
    "clerks": ["值班人員A", "值班人員B"],
    "backup_channel": {
        "zh": "請派人到村辦公室通報，或使用衛星電話",
        "en": "Send a runner to the village office or use the satellite phone",
    },
    "ports": {"desk_http": 8000, "eoc_http": 8100, "emu_udp": 9001, "eoc_udp": 9002, "emu_control_http": 9100},
    "paths": {"desk_db": "data/run/desk.db", "eoc_db": "data/run/eoc.db", "models_dir": None},
    "asr": {"model": "small", "fast_model": "base", "compute_type": "int8", "language": "zh",
            "beam_size": 1, "max_seconds": 30, "cpu_threads": 0},
    "llm": {"enabled": True, "url": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b",
            "timeout_s": 15, "warmup_timeout_s": 60, "num_predict": 320, "keep_alive": -1,
            "min_tokens_per_s": 4, "use_mmap": True},
    "pipeline": {"backlog_fast_mode": 3},
    "triage": {"sla_minutes": {"emergency": 3, "1": 30, "2": 240, "3": 1440},
               "unconfirmed_p1_reminder_s": 120, "unconfirmed_p1_alert_s": 300},
    "net": {"mtu": 200, "window": 2, "rto_initial_s": 3.0, "rto_min_s": 0.5, "rto_max_s": 30,
            "rto_backoff_cap": 2.0, "heartbeat_s": 5, "probe_max_s": 5, "down_after_timeouts": 4,
            "hmac_key_hex": None},
    # keep_audio and scrub_national_id are SPEC config keys, but the code always takes the safe side:
    # audio is never written to disk and national IDs are always scrubbed, whatever these say.
    # privacy_view_default sets the desk page's privacy toggle at load.
    "privacy": {"keep_audio": False, "scrub_national_id": True, "privacy_view_default": True},
    "voice_baseline_bytes_per_s": 2000,
    # Extra desks the EOC accepts: {"<desk_id>": "<hmac hex>"} (optional).
    "eoc": {"desk_keys": {}},
}


def deep_merge(base, over):
    """Return a new dict: `over` merged recursively over `base`."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_path(p):
    """Resolve a config path against the repo root (None stays None)."""
    if p is None or p == "":
        return None
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


def default_config_path():
    return ROOT / "config.json"


def _valid_key(hexstr):
    if not isinstance(hexstr, str) or len(hexstr) < 32:
        return False
    try:
        bytes.fromhex(hexstr)
        return True
    except ValueError:
        return False


def make_config(overrides=None):
    """In-memory config for tests: overrides merged over DEFAULTS, key generated if absent."""
    cfg = deep_merge(DEFAULTS, overrides or {})
    if not _valid_key(cfg["net"].get("hmac_key_hex")):
        cfg["net"]["hmac_key_hex"] = secrets.token_hex(32)
    return cfg


def load_config(path=None, ensure_key=True):
    """Load the user config file merged over DEFAULTS.

    `path` None -> <root>/config.json. A relative path resolves against the repo root.
    If the file does not exist it is created from config.example.json. When the HMAC
    key is missing it is generated and persisted (ensure_key=True).
    """
    path = Path(path) if path else default_config_path()
    if not path.is_absolute():
        path = ROOT / path
    user = {}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
    else:
        example = ROOT / "config.example.json"
        if example.exists():
            user = json.loads(example.read_text(encoding="utf-8"))
    cfg = deep_merge(DEFAULTS, user)
    if ensure_key and not _valid_key(cfg["net"].get("hmac_key_hex")):
        key = secrets.token_hex(32)
        cfg["net"]["hmac_key_hex"] = key
        user.setdefault("net", {})["hmac_key_hex"] = key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg["_path"] = str(path)
    return cfg


def save_config(cfg, path):
    """Write a config dict (minus private keys) to `path` as UTF-8 JSON."""
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hmac_key(cfg):
    return bytes.fromhex(cfg["net"]["hmac_key_hex"])


def eoc_keys(cfg):
    """{desk_id: key bytes} accepted by the EOC receiver."""
    keys = {int(cfg["desk_id"]): hmac_key(cfg)}
    for desk_id, hexkey in (cfg.get("eoc", {}).get("desk_keys") or {}).items():
        if _valid_key(hexkey):
            keys[int(desk_id)] = bytes.fromhex(hexkey)
    return keys


def db_path(cfg, which):
    """Resolved path of 'desk_db' or 'eoc_db'."""
    return resolve_path(cfg["paths"][which])
