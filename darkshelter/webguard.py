"""Request guards shared by the desk and EOC apps (REDTEAM.md H2, M5).

- Host must be a loopback name (127.0.0.1, localhost, [::1]), with no port or this server's port.
  This stops DNS rebinding: a page on evil.example can't talk to us under its own name.
- State-changing requests must not come from another site: a foreign Origin, or
  Sec-Fetch-Site cross-site/same-site, gets 403.
- JSON routes need Content-Type application/json, so a cross-site form or text/plain
  POST (a "simple" request, no CORS preflight) can't reach them. Routes that take a raw
  body (audio) need the custom header X-DarkShelter: 1, which forces a preflight.
- No page may be shown inside another site's frame (X-Frame-Options + CSP frame-ancestors).
  Otherwise a hostile page could frame the desk and steer a clerk's click onto 結案: that
  click would be a same-origin request, which the checks above rightly allow (clickjacking).
"""
from flask import jsonify, request

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
RAW_HEADER = "X-DarkShelter"


def split_host(host):
    """'127.0.0.1:8000' -> ('127.0.0.1', '8000'); '[::1]:80' -> ('[::1]', '80'); 'localhost' -> ('localhost', None)."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return host, None
        name, rest = host[:end + 1], host[end + 1:]
        return name, (rest[1:] if rest.startswith(":") else None)
    if host.count(":") == 1:
        name, port = host.split(":")
        return name, port
    return host, None


def host_ok(host, ports):
    name, port = split_host(host)
    if name not in LOOPBACK_HOSTS:
        return False
    if port in (None, ""):
        return True
    return port in {str(p) for p in ports if p not in (None, "")}


def origin_ok():
    origin = request.headers.get("Origin")
    if origin is not None and origin.rstrip("/").lower() != request.host_url.rstrip("/").lower():
        return False
    if (request.headers.get("Sec-Fetch-Site") or "").lower() in ("cross-site", "same-site"):
        return False
    return True


def _deny(error, zh, en, code):
    return jsonify({"error": error, "zh": zh, "en": en}), code


def install(app, ports_fn, raw_endpoints=()):
    """Register the guard. ports_fn() -> the configured port(s); raw_endpoints take a non-JSON body."""
    raw_endpoints = set(raw_endpoints)

    @app.before_request
    def _guard():
        ports = set(str(p) for p in (ports_fn() or ()))
        sp = request.environ.get("SERVER_PORT")
        if sp:
            ports.add(str(sp))
        if not host_ok(request.host, ports):
            return _deny("bad_host", "不接受這個主機名稱", "Host not allowed", 400)
        if request.method not in UNSAFE_METHODS:
            return None
        if not origin_ok():
            return _deny("cross_origin", "拒絕來自其他網站的請求", "Cross-site request refused", 403)
        if request.endpoint in raw_endpoints:
            if request.headers.get(RAW_HEADER) != "1":
                return _deny("missing_header", "缺少 X-DarkShelter 標頭", "Missing X-DarkShelter header", 403)
        elif request.mimetype != "application/json":
            return _deny("json_required", "請用 JSON 格式", "Content-Type must be application/json", 415)
        return None

    @app.after_request
    def _no_framing(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return resp

    return _guard
