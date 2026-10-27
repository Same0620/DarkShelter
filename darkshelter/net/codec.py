"""Binary codec for the thin link (SPEC 8.1). Big-endian `struct`.

Datagram = header (7 B: ver u8 = 1, type u8, desk_id u16, boot_id u8, msg_seq u16)
           + count u8 + body + HMAC-SHA256(key, everything before)[:8]
Fixed overhead: 16 B. Every datagram built here is <= the given MTU.

Records:
  digest (18 B): ticket_seq u32, version u16, prio_cat u8 (priority bits 7-6, category 5-0),
                 item_code u16, quantity u16, subject_group u8, flags u8, age_min u16,
                 deadline_h u8, zone u16
  DATA_SUMMARY record: digest + len u8 + UTF-8 summary (<= 60 B)
  ACK entry (8 B): ticket_seq u32, version u16, level u8, eoc_status u8
  HEARTBEAT body (17 B, count = 0): people u16, elderly u8, infant_child u8, disabled u8,
                 pregnant u8, q_p1 u16, q_p2 u16, q_p3 u16, battery u8 (255 unknown),
                 status_seq_applied u32  -> a 33 B datagram
  HB_REPLY entry (9 B): ticket_seq u32, eoc_status u8, status_seq u32

`unpack` raises only BadMac, BadVersion or Truncated, whatever bytes it is given.
"""
import hashlib
import hmac
import math
import struct

VERSION = 1
DATA_DIGEST = 1
DATA_SUMMARY = 2
ACK = 3
HEARTBEAT = 4
HB_REPLY = 5
TYPE_NAMES = {DATA_DIGEST: "DATA_DIGEST", DATA_SUMMARY: "DATA_SUMMARY", ACK: "ACK",
              HEARTBEAT: "HEARTBEAT", HB_REPLY: "HB_REPLY"}

HEADER = struct.Struct(">BBHBH")          # 7 B
MAC_LEN = 8
OVERHEAD = HEADER.size + 1 + MAC_LEN      # 16 B
DIGEST = struct.Struct(">IHBHHBBHBH")     # 18 B
ACK_ENTRY = struct.Struct(">IHBB")        # 8 B
CENSUS = struct.Struct(">HBBBBHHHBI")     # 17 B
HB_ENTRY = struct.Struct(">IBI")          # 9 B
SUMMARY_MAX = 60
MAX_COUNT = 255
HEARTBEAT_SIZE = OVERHEAD + CENSUS.size   # 33 B

CENSUS_FIELDS = ("people", "elderly", "infant_child", "disabled", "pregnant",
                 "q_p1", "q_p2", "q_p3", "battery", "status_seq_applied")
_CENSUS_MAX = (0xFFFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFF, 0xFFFFFFFF)


class CodecError(Exception):
    """Base class of every error `unpack` may raise."""


class BadMac(CodecError):
    pass


class BadVersion(CodecError):
    pass


class Truncated(CodecError):
    pass


class Message:
    """A decoded datagram."""
    __slots__ = ("ver", "type", "desk_id", "boot_id", "msg_seq", "count", "records", "census", "size")

    def __init__(self, ver, mtype, desk_id, boot_id, msg_seq, count, size):
        self.ver, self.type, self.desk_id = ver, mtype, desk_id
        self.boot_id, self.msg_seq, self.count, self.size = boot_id, msg_seq, count, size
        self.records = []
        self.census = None

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, str(self.type))

    def to_dict(self):
        return {"ver": self.ver, "type": self.type_name, "desk_id": self.desk_id, "boot_id": self.boot_id,
                "msg_seq": self.msg_seq, "count": self.count, "records": self.records,
                "census": self.census, "size": self.size}

    def __repr__(self):
        return f"Message({self.type_name}, desk={self.desk_id}, boot={self.boot_id}, seq={self.msg_seq}, n={self.count})"


def clamp(v, lo, hi):
    """Clamp anything number-like to the integer range [lo, hi] (garbage -> lo)."""
    if isinstance(v, float):
        if math.isnan(v):
            return lo
        if math.isinf(v):
            return hi if v > 0 else lo
        v = math.floor(v)
    try:
        v = int(v)
    except (TypeError, ValueError, OverflowError):
        return lo
    return lo if v < lo else hi if v > hi else v


def clamp_deadline_h(v):
    """255 = no deadline; otherwise floor(hours_left) clamped to [0, 254] (overdue -> 0)."""
    if v is None:
        return 255
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 255:
        return 255
    return clamp(v, 0, 254)


def utf8_truncate(s, max_bytes=SUMMARY_MAX):
    """UTF-8 bytes of `s`, cut on a character boundary to <= max_bytes."""
    if s is None:
        return b""
    if isinstance(s, (bytes, bytearray)):
        s = bytes(s).decode("utf-8", "replace")
    out = bytearray()
    for ch in str(s):
        b = ch.encode("utf-8", "replace")
        if len(out) + len(b) > max_bytes:
            break
        out += b
    return bytes(out)


# ------------------------------------------------------------------ records

def encode_digest(v):
    """18-byte digest of a wire view (every integer clamped to its field)."""
    prio = clamp(v.get("priority"), 0, 3)
    cat = clamp(v.get("category"), 0, 63)
    return DIGEST.pack(
        clamp(v.get("seq"), 0, 0xFFFFFFFF),
        clamp(v.get("version"), 0, 0xFFFF),
        (prio << 6) | cat,
        clamp(v.get("item_code"), 0, 0xFFFF),
        clamp(v.get("quantity"), 0, 0xFFFF),
        clamp(v.get("subject_group"), 0, 0xFF),
        clamp(v.get("flags"), 0, 0xFF),
        clamp(v.get("age_min"), 0, 0xFFFF),
        clamp_deadline_h(v.get("deadline_h")),
        clamp(v.get("zone"), 0, 0xFFFF),
    )


def encode_summary_record(v):
    """Digest + len u8 + UTF-8 summary (<= 60 B). Raises on a view that cannot be packed."""
    if not isinstance(v, dict):
        raise TypeError("view must be a dict")
    summary = utf8_truncate(v.get("summary") or "")
    return encode_digest(v) + bytes([len(summary)]) + summary


def summary_record_size(v):
    return DIGEST.size + 1 + len(utf8_truncate(v.get("summary") or ""))


def _decode_digest(buf, off):
    (seq, version, prio_cat, item_code, quantity, subject_group, flags, age_min,
     deadline_h, zone) = DIGEST.unpack_from(buf, off)
    return {"seq": seq, "version": version, "priority": prio_cat >> 6, "category": prio_cat & 0x3F,
            "item_code": item_code, "quantity": quantity, "subject_group": subject_group, "flags": flags,
            "age_min": age_min, "deadline_h": deadline_h, "zone": zone}


# ------------------------------------------------------------------ datagrams

def _seal(key, mtype, desk_id, boot_id, msg_seq, count, body):
    head = HEADER.pack(VERSION, mtype, clamp(desk_id, 0, 0xFFFF), clamp(boot_id, 0, 0xFF),
                       clamp(msg_seq, 0, 0xFFFF)) + bytes([clamp(count, 0, MAX_COUNT)])
    data = head + bytes(body)
    return data + hmac.new(key, data, hashlib.sha256).digest()[:MAC_LEN]


def pack_summary(views, mtu, key, desk_id, boot_id, msg_seq):
    """Fill one DATA_SUMMARY datagram with views in order -> (bytes, n_packed).

    Stops at the first view that does not fit (or cannot be encoded). (b"", 0) if none fits.
    """
    limit = int(mtu) - OVERHEAD
    body = bytearray()
    n = 0
    for v in views:
        if n >= MAX_COUNT:
            break
        try:
            rec = encode_summary_record(v)
        except Exception:
            break
        if len(body) + len(rec) > limit:
            break
        body += rec
        n += 1
    if n == 0:
        return b"", 0
    return _seal(key, DATA_SUMMARY, desk_id, boot_id, msg_seq, n, body), n


def pack_digest(views, mtu, key, desk_id, boot_id, msg_seq):
    """DATA_DIGEST datagram (kept in the codec; the Stage I sender does not use it)."""
    limit = int(mtu) - OVERHEAD
    body = bytearray()
    n = 0
    for v in views:
        if n >= MAX_COUNT or len(body) + DIGEST.size > limit:
            break
        try:
            body += encode_digest(v)
        except Exception:
            break
        n += 1
    if n == 0:
        return b"", 0
    return _seal(key, DATA_DIGEST, desk_id, boot_id, msg_seq, n, body), n


def _entry_tuple(e, names):
    if isinstance(e, dict):
        return tuple(e.get(k) for k in names)
    return tuple(e)


def pack_ack(entries, key, desk_id, boot_id, msg_seq, mtu=None):
    """ACK datagram; the header echoes the acked datagram's boot_id and msg_seq.

    entries: (ticket_seq, version, level, eoc_status) tuples or dicts.
    """
    limit = (int(mtu) - OVERHEAD) if mtu else MAX_COUNT * ACK_ENTRY.size
    body = bytearray()
    n = 0
    for e in entries:
        if n >= MAX_COUNT or len(body) + ACK_ENTRY.size > limit:
            break
        seq, version, level, status = _entry_tuple(e, ("seq", "version", "level", "eoc_status"))
        body += ACK_ENTRY.pack(clamp(seq, 0, 0xFFFFFFFF), clamp(version, 0, 0xFFFF), clamp(level, 0, 0xFF),
                               clamp(status, 0, 0xFF))
        n += 1
    return _seal(key, ACK, desk_id, boot_id, msg_seq, n, body)


def pack_heartbeat(census, key, desk_id, boot_id, msg_seq):
    """HEARTBEAT (count = 0) carrying the census -> always 33 bytes."""
    census = census or {}
    vals = []
    for name, hi in zip(CENSUS_FIELDS, _CENSUS_MAX):
        v = census.get(name)
        if name == "battery" and v is None:
            v = 255
        vals.append(clamp(v, 0, hi))
    return _seal(key, HEARTBEAT, desk_id, boot_id, msg_seq, 0, CENSUS.pack(*vals))


def pack_hb_reply(entries, key, desk_id, boot_id, msg_seq, mtu=200):
    """HB_REPLY with as many (ticket_seq, eoc_status, status_seq) as fit -> (bytes, n)."""
    limit = int(mtu) - OVERHEAD
    body = bytearray()
    n = 0
    for e in entries:
        if n >= MAX_COUNT or len(body) + HB_ENTRY.size > limit:
            break
        seq, status, sseq = _entry_tuple(e, ("seq", "status", "status_seq"))
        body += HB_ENTRY.pack(clamp(seq, 0, 0xFFFFFFFF), clamp(status, 0, 0xFF), clamp(sseq, 0, 0xFFFFFFFF))
        n += 1
    return _seal(key, HB_REPLY, desk_id, boot_id, msg_seq, n, body), n


# ------------------------------------------------------------------ unpack

def _key_for(keys, desk_id):
    if keys is None:
        return None
    if isinstance(keys, (bytes, bytearray)):
        return bytes(keys)
    try:
        return keys.get(desk_id)
    except Exception:
        return None


def peek_header(datagram):
    """(ver, type, desk_id, boot_id, msg_seq) without verifying anything, or None."""
    try:
        data = bytes(datagram)
        if len(data) < HEADER.size:
            return None
        return HEADER.unpack_from(data, 0)
    except Exception:
        return None


def unpack(datagram, keys):
    """Verify and decode a datagram. Raises only BadMac, BadVersion or Truncated.

    keys: {desk_id: key bytes} (or a single key used for any desk_id).
    """
    try:
        data = bytes(datagram)
    except Exception:
        raise Truncated("not a byte string")
    if len(data) < OVERHEAD:
        raise Truncated(f"datagram of {len(data)} B is shorter than {OVERHEAD} B")
    if data[0] != VERSION:
        raise BadVersion(f"version {data[0]}")
    ver, mtype, desk_id, boot_id, msg_seq = HEADER.unpack_from(data, 0)
    key = _key_for(keys, desk_id)
    if not key:
        raise BadMac(f"no key for desk {desk_id}")
    body_end = len(data) - MAC_LEN
    try:
        expected = hmac.new(key, data[:body_end], hashlib.sha256).digest()[:MAC_LEN]
    except Exception:
        raise BadMac("bad key")
    if not hmac.compare_digest(expected, data[body_end:]):
        raise BadMac("MAC mismatch")
    count = data[HEADER.size]
    body = data[HEADER.size + 1:body_end]
    msg = Message(ver, mtype, desk_id, boot_id, msg_seq, count, len(data))
    off = 0
    try:
        if mtype == DATA_DIGEST:
            for _ in range(count):
                rec = _decode_digest(body, off)
                rec["level"] = 1
                rec["summary"] = None
                msg.records.append(rec)
                off += DIGEST.size
        elif mtype == DATA_SUMMARY:
            for _ in range(count):
                rec = _decode_digest(body, off)
                off += DIGEST.size
                if off >= len(body):
                    raise Truncated("missing summary length")
                n = body[off]
                off += 1
                if n > SUMMARY_MAX or off + n > len(body):
                    raise Truncated("summary overruns the body")
                rec["summary"] = body[off:off + n].decode("utf-8", "replace")
                rec["level"] = 2
                off += n
                msg.records.append(rec)
        elif mtype == ACK:
            for _ in range(count):
                seq, version, level, status = ACK_ENTRY.unpack_from(body, off)
                msg.records.append({"seq": seq, "version": version, "level": level, "eoc_status": status})
                off += ACK_ENTRY.size
        elif mtype == HEARTBEAT:
            if count != 0 or len(body) != CENSUS.size:
                raise Truncated("bad heartbeat body")
            msg.census = dict(zip(CENSUS_FIELDS, CENSUS.unpack_from(body, 0)))
            off = CENSUS.size
        elif mtype == HB_REPLY:
            for _ in range(count):
                seq, status, sseq = HB_ENTRY.unpack_from(body, off)
                msg.records.append({"seq": seq, "status": status, "status_seq": sseq})
                off += HB_ENTRY.size
        else:
            raise Truncated(f"unknown type {mtype}")
    except struct.error as e:
        raise Truncated(str(e))
    if off != len(body):
        raise Truncated(f"{len(body) - off} trailing bytes")
    return msg
