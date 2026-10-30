"""Web capture (docs/SDD-web-capture.md): facts, pictures and a logo from the owner's web pages, for Motion and the
other videos.

This part: the request guard (OWASP SSRF Prevention, "case 2: arbitrary URLs"), robots.txt (RFC 9309, with the owner's
own sites exempt) and the own-sites list. Every request MediaForge or its browser makes for a capture goes through
`check_url`; robots.txt is checked for every page it opens.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from mediaforge import __version__
from mediaforge.config import settings

SCHEMES = ("http", "https")
AGENT = "MediaForge"  # our robots.txt product token
USER_AGENT_SUFFIX = f"{AGENT}/{__version__} (+local; personal use)"
OWN_SITES_SEED = ("aisme.vn", "easyvisarun.com", "tradeswomanmerch.com")  # owner decision §10.2
MAX_URLS = 3
MAX_REDIRECTS = 5  # RFC 9309 §2.3.1.2: at least five hops for robots.txt; the same for pictures


class Blocked(ValueError):
    """A URL the guard refuses (scheme, address, robots.txt)."""


# ------------------------------------------------------------------ SSRF guard

def resolve(host: str) -> list[str]:
    """Every A/AAAA address of host (module-level so tests can stand in a resolver)."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(i[4][0]).split("%")[0] for i in infos))


def public_ip(addr: str) -> bool:
    """True for a globally routable unicast address: not loopback, RFC 1918, link-local (cloud metadata), CGNAT,
    unique-local, multicast, reserved or unspecified. IPv4-mapped IPv6 is judged as the IPv4 it carries."""
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local
                                 or ip.is_private or ip.is_reserved)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def check_url(url: str) -> str:
    """The URL when MediaForge may fetch it; Blocked otherwise. Call it again for every redirect hop."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in SCHEMES:
        raise Blocked(f"chỉ nhận http/https: {url[:120]}")
    host = host_of(url)
    if not host:
        raise Blocked(f"thiếu tên miền: {url[:120]}")
    if parts.username or parts.password:
        raise Blocked("địa chỉ có tên đăng nhập/mật khẩu")
    if host in settings.web_capture_allow_hosts:
        return url
    try:
        addrs = resolve(host)
    except (OSError, UnicodeError) as e:
        raise Blocked(f"không phân giải được {host}: {e}") from e
    if not addrs:
        raise Blocked(f"không phân giải được {host}")
    for a in addrs:
        try:
            ok = public_ip(a)
        except ValueError:
            ok = False
        if not ok:
            raise Blocked(f"{host} trỏ tới địa chỉ nội bộ {a}")
    return url


def domain_match(host: str, domain: str) -> bool:
    """A domain matches itself and its subdomains (www.aisme.vn ∈ aisme.vn)."""
    host, domain = host.lower().rstrip("."), domain.lower().strip().rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def site_of(host: str) -> str:
    """The site a capture may stay on: the host without a leading www."""
    return host.lower().removeprefix("www.")


# ------------------------------------------------------------------ own sites (robots.txt exempt, §10.2 / §12.4)

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def _store() -> Path:
    return settings.sub("webcapture")


def _clean_domain(d: Any) -> str:
    s = unicodedata.normalize("NFC", str(d or "")).strip().lower()
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].split(":")[0].rstrip(".")
    try:
        s = s.encode("idna").decode("ascii") if s else s
    except UnicodeError:
        return ""
    return s.removeprefix("www.") if DOMAIN_RE.match(s) else ""


def own_sites() -> list[str]:
    f = _store() / "own_sites.json"
    try:
        data = json.loads(f.read_text("utf-8")) if f.exists() else list(OWN_SITES_SEED)
    except (OSError, ValueError):
        data = list(OWN_SITES_SEED)
    return [d for d in dict.fromkeys(_clean_domain(x) for x in data if isinstance(x, str)) if d]


def save_own_sites(domains: list[Any]) -> list[str]:
    """Keep the valid domains (one per entry; a pasted URL keeps its host), deduplicated, in order."""
    clean = [d for d in dict.fromkeys(_clean_domain(x) for x in domains) if d][:100]
    f = _store() / "own_sites.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2))
    tmp.replace(f)
    return clean


def is_own(host: str, sites: list[str] | None = None) -> bool:
    return any(domain_match(host, d) for d in (own_sites() if sites is None else sites))


# ------------------------------------------------------------------ robots.txt (RFC 9309)
# urllib.robotparser is not used: it matches by prefix with the first rule winning, and knows neither `*` nor `$`
# (easyvisarun.com has `Disallow: /*?chat=`). RFC 9309 §2.2.2: the longest matching rule wins, Allow on a tie.

Rules = list[tuple[bool, str]]  # (allow, path pattern)


def parse_robots(text: str, agent: str = AGENT) -> Rules:
    """The rules for `agent`: the groups naming its product token (case-insensitive), combined; else the `*` groups."""
    groups: list[tuple[list[str], Rules]] = []
    agents: list[str] = []
    rules: Rules = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # a user-agent line after rules starts a new group
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(val.lower())
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            if val:  # an empty Disallow allows everything: no rule
                rules.append((key == "allow", val))
    if agents:
        groups.append((agents, rules))
    mine = [r for a, rs in groups if agent.lower() in a for r in rs]
    if any(agent.lower() in a for a, _ in groups):
        return mine
    return [r for a, rs in groups if "*" in a for r in rs]


def _pattern(p: str) -> re.Pattern[str]:
    end = p.endswith("$")
    body = re.escape(p[:-1] if end else p).replace(r"\*", ".*")
    return re.compile(body + ("$" if end else ""))


def robots_allows(rules: Rules, path: str) -> bool:
    """path = the URL's path plus its query ("/a?b=1"). /robots.txt itself is always allowed."""
    if path.split("?")[0] == "/robots.txt":
        return True
    best: tuple[int, bool] | None = None
    for allow, pat in rules:
        if _pattern(pat).match(path):
            k = (len(pat), allow)  # longest pattern, Allow wins a tie
            if best is None or k > best:
                best = k
    return True if best is None else best[1]


def path_of(url: str) -> str:
    s = urlsplit(url)
    return (s.path or "/") + (f"?{s.query}" if s.query else "")


Fetch = Callable[[str], tuple[int, bytes]]  # url → (status, body); raises on a network error


def robots_rules(origin: str, fetch: Fetch) -> tuple[Rules | None, str]:
    """(rules, note) for an origin like https://shop.vn. RFC 9309 §2.3.1: 2xx → its rules; 4xx → no rules (all
    allowed); 5xx or unreachable → complete disallow (rules None)."""
    try:
        status, body = fetch(origin + "/robots.txt")
    except Exception as e:  # noqa: BLE001 — unreachable counts as "complete disallow"
        return None, f"không đọc được robots.txt ({type(e).__name__}): coi như cấm"
    if 200 <= status < 300:
        return parse_robots(body[:500_000].decode("utf-8", "replace")), "robots.txt"
    if 400 <= status < 500:
        return [], f"robots.txt {status}: không có luật"
    return None, f"robots.txt lỗi {status}: coi như cấm"


class Robots:
    """robots.txt per origin for one capture; the owner's own sites skip it (§10.2)."""

    def __init__(self, fetch: Fetch, own: list[str] | None = None):
        self.fetch = fetch
        self.own = own_sites() if own is None else own
        self.cache: dict[str, tuple[Rules | None, str]] = {}
        self.notes: dict[str, str] = {}

    def check(self, url: str) -> tuple[bool, str]:
        host = host_of(url)
        if is_own(host, self.own):
            self.notes[host] = "web của tôi: bỏ qua robots.txt"
            return True, self.notes[host]
        s = urlsplit(url)
        origin = f"{s.scheme}://{s.netloc}"
        if origin not in self.cache:
            self.cache[origin] = robots_rules(origin, self.fetch)
        rules, note = self.cache[origin]
        ok = rules is not None and robots_allows(rules, path_of(url))
        self.notes[host] = note if ok or rules is None else f"{note}: cấm {path_of(url)[:80]}"
        return ok, self.notes[host]


# ------------------------------------------------------------------ guarded HTTP (robots.txt, pictures)

def user_agent(base: str = "Mozilla/5.0") -> str:
    return f"{base} {USER_AGENT_SUFFIX}"


def http_get(url: str, *, max_bytes: int, accept: tuple[str, ...] = (), timeout: float = 20.0,
             client: Any = None) -> tuple[int, bytes, str, str]:
    """GET through the guard: every redirect hop is checked before it is requested. Returns (status, body,
    content type, final URL). The body stops at max_bytes (Blocked when larger); `accept` limits content types by
    prefix ("image/")."""
    import httpx

    own = client is None
    c = client or httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": user_agent()})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            with c.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if accept and r.status_code < 300 and not ctype.startswith(accept):
                    raise Blocked(f"không phải ảnh ({ctype or '?'}): {url[:120]}")
                if int(r.headers.get("content-length") or 0) > max_bytes:
                    raise Blocked(f"quá lớn: {url[:120]}")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise Blocked(f"quá lớn: {url[:120]}")
                return r.status_code, bytes(buf), ctype, url
        raise Blocked(f"quá nhiều chuyển hướng: {url[:120]}")
    finally:
        if own:
            c.close()


def robots_fetch(url: str) -> tuple[int, bytes]:
    status, body, _, _ = http_get(url, max_bytes=500_000, timeout=10)
    return status, body
