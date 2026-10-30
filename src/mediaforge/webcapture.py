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
             client: Any = None, headers: dict[str, str] | None = None) -> tuple[int, bytes, str, str]:
    """GET through the guard: every redirect hop is checked before it is requested. Returns (status, body,
    content type, final URL). The body stops at max_bytes (Blocked when larger); `accept` limits content types by
    prefix ("image/")."""
    import httpx

    own = client is None
    c = client or httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": user_agent()})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            with c.stream("GET", url, headers=headers) as r:
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


# ------------------------------------------------------------------ facts (§4 "Facts, in order of trust")

PRICE = re.compile(r"(?:\d[\d.,\s]{0,14}\d|\d)\s?(?:đ|₫|vnđ|vnd|usd|aud|\$|€|£)(?![a-z])|(?:\$|€|£|a\$|us\$)\s?\d[\d.,]*",
                   re.I)
FACT_CHARS = 300  # each fact is cut to this before it reaches an LLM (§6)


def _nfc(s: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s if s is not None else ""))).strip()


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("@type")
    return {str(x).split("/")[-1] for x in (t if isinstance(t, list) else [t]) if x}


def jsonld_nodes(blocks: list[str]) -> list[dict[str, Any]]:
    """Every typed object in the page's JSON-LD blocks: top-level lists, @graph and nested values included."""
    out: list[dict[str, Any]] = []

    def walk(v: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
        elif isinstance(v, dict):
            if v.get("@type"):
                out.append(v)
            for k, x in v.items():
                if k != "@context":
                    walk(x, depth + 1)

    for b in blocks:
        try:
            walk(json.loads(re.sub(r"^\s*<!--|-->\s*$", "", b or "").strip()))
        except ValueError:
            continue
    return out


def _url_of(v: Any) -> list[str]:
    """image / logo values: a string, an ImageObject, or a list of either."""
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return _url_of(v.get("url") or v.get("contentUrl") or v.get("@id"))
    if isinstance(v, list):
        return [u for x in v for u in _url_of(x)]
    return []


def _name_of(v: Any) -> str:
    if isinstance(v, dict):
        return _nfc(v.get("name"))
    if isinstance(v, list):
        return next((n for n in (_name_of(x) for x in v) if n), "")
    return _nfc(v)


def _price_of(offers: Any) -> str:
    for o in offers if isinstance(offers, list) else [offers]:
        if not isinstance(o, dict):
            continue
        cur = _nfc(o.get("priceCurrency"))
        if o.get("price") not in (None, ""):
            return f"{_nfc(o['price'])} {cur}".strip()
        if o.get("lowPrice") not in (None, ""):  # AggregateOffer
            hi = f"–{_nfc(o['highPrice'])}" if o.get("highPrice") not in (None, "") else ""
            return f"{_nfc(o['lowPrice'])}{hi} {cur}".strip()
    return ""


def facts_from(scan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(facts, picture candidates, logo candidates) from one page's scan. Each keeps where it came from."""
    url = scan["url"]
    facts: list[dict[str, Any]] = []
    pics: list[dict[str, Any]] = []
    logos: list[dict[str, Any]] = []

    def fact(key: str, value: Any, source: str, sel: str | None = None) -> None:
        v = _nfc(value)[:FACT_CHARS]
        if v and not any(f["key"] == key and f["value"] == v for f in facts):
            facts.append({"key": key, "value": v, "source": source, "url": url, **({"sel": sel} if sel else {})})

    nodes = jsonld_nodes(scan.get("jsonld") or [])
    for n in nodes:
        t = _types(n)
        if t & {"Product", "ProductGroup"}:
            fact("name", n.get("name"), "jsonld:Product.name")
            fact("description", n.get("description"), "jsonld:Product.description")
            fact("brand", _name_of(n.get("brand")), "jsonld:Product.brand")
            fact("price", _price_of(n.get("offers")), "jsonld:Product.offers")
            pics += [{"url": urljoin(url, u), "kind": "product", "source": "jsonld:image"} for u in _url_of(n.get("image"))]
        if t & {"Organization", "Corporation", "LocalBusiness", "Store", "OnlineStore", "Brand"}:
            logos += [{"url": urljoin(url, u), "source": "jsonld:logo"} for u in _url_of(n.get("logo"))]
            fact("brand", n.get("name"), "jsonld:Organization.name")
        if "Offer" in t and not any(f["key"] == "price" for f in facts):
            fact("price", _price_of(n), "jsonld:Offer")
    meta = scan.get("meta") or {}
    fact("name", meta.get("og:title"), "og:title")
    fact("description", meta.get("og:description"), "og:description")
    fact("brand", meta.get("og:site_name"), "og:site_name")
    if meta.get("og:image"):
        pics.append({"url": urljoin(url, meta["og:image"]), "kind": "product" if not pics else "image", "source": "og:image",
                     "alt": meta.get("og:image:alt", "")})
    fact("name", scan.get("title"), "meta:title")
    fact("description", meta.get("description"), "meta:description")
    for h in scan.get("headings") or []:
        fact("heading", h["text"], f"text:{h['tag']}", h.get("sel"))
    for p in (scan.get("prices") or [])[:8]:
        fact("price", p["text"], "text:price", p.get("sel"))
    for im in scan.get("images") or []:
        if im.get("header") and im.get("logo"):
            logos.append({"url": im["src"], "source": "header:img", "sel": im.get("sel")})
        elif im.get("visible") and min(im.get("w") or 0, im.get("h") or 0) >= MIN_SIDE:
            pics.append({"url": im["src"], "kind": "image", "source": "page:img", "alt": im.get("alt", ""),
                         "sel": im.get("sel"), "area": im.get("area", 0)})
    icons = sorted(scan.get("icons") or [], key=lambda i: ("apple-touch-icon" not in i["rel"], -_icon_size(i["sizes"])))
    for i in icons:
        if "apple-touch-icon" in i["rel"] or _icon_size(i["sizes"]) >= MIN_LOGO:
            logos.append({"url": i["href"], "source": "link:" + i["rel"].replace(" ", "-")})
    # page images: largest rendered first, after the declared product pictures
    declared = [p for p in pics if p["source"] != "page:img"]
    page = sorted((p for p in pics if p["source"] == "page:img"), key=lambda p: -p.get("area", 0))
    return facts, declared + page, logos


def _icon_size(sizes: str) -> int:
    m = re.findall(r"(\d+)x(\d+)", sizes or "")
    return max((int(a) for a, _ in m), default=0)


# ------------------------------------------------------------------ pictures

MIN_SIDE = 500  # product/page pictures: short side
MIN_LOGO = 128  # logos: long side (plain favicons are refused)
MAX_PICTURE = 15 * 1024 * 1024
MAX_TOTAL = 40 * 1024 * 1024
MAX_PICTURES = 12
RECOMMENDED = 4
RANK = {"product": 0, "block": 1, "image": 2, "hero": 3, "page": 4}


def dhash(im: Any) -> int:
    """64-bit difference hash (perceptual duplicate check, Pillow only)."""
    g = im.convert("L").resize((9, 8))
    px = g.tobytes()
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def near_duplicate(h: int, seen: list[int], bits: int = 6) -> bool:
    return any((h ^ s).bit_count() <= bits for s in seen)


def save_picture(data: bytes, dest: Path, *, max_side: int = 2400) -> tuple[Path, int, int, int] | None:
    """Decode with Pillow and save as PNG (alpha) or JPEG under dest's stem; (path, w, h, dhash) or None."""
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as im:
            im.load()
            w, h = im.size
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
            hsh = dhash(im)
            if max(w, h) > max_side:
                im.thumbnail((max_side, max_side))
            path = dest.with_suffix(".png" if alpha else ".jpg")
            im.save(path, **({} if alpha else {"quality": 90}))
            return path, w, h, hsh
    except Exception:  # noqa: BLE001 — not an image Pillow reads (avif without codec, broken file)
        return None


# ------------------------------------------------------------------ browser (§4 "Browser")

PAGE_TIMEOUT = 20.0  # per page load
URL_BUDGET = 60.0  # per URL in total
VIEWPORT = {"width": 1440, "height": 900}
REDIRECT = (301, 302, 303, 307, 308)


class Session:
    """One guarded Chromium context per capture: fresh (no stored cookies), service workers blocked, no downloads,
    closed at the end. Every http(s) request goes through `_route`, which checks it and fetches it itself one hop at a
    time: Playwright does not route redirect hops (checked 2026-10-31 on 1.64: neither continue_() nor
    fetch()+fulfill() shows the hops), so a navigation redirect becomes a client-side redirect — a new, routed request —
    and a sub-resource redirect is followed here, each hop checked before it is requested."""

    def __init__(self, sites: list[str], robots: Robots):
        self.sites = sites  # main-frame navigations stay on these sites (and their subdomains)
        self.robots = robots
        self.blocked: list[str] = []
        self._hosts: dict[str, bool] = {}
        self.stub: str | None = None  # the target of the last client-side redirect we served
        self.status: int | None = None  # HTTP status of the last main-frame document

    def __enter__(self) -> Session:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            try:
                self.browser = self._pw.chromium.launch()
            except Exception:  # noqa: BLE001 — Playwright's Chromium not downloaded: installed Chrome
                self.browser = self._pw.chromium.launch(channel="chrome")
            self.ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                       f"Chrome/{self.browser.version} Safari/537.36 {USER_AGENT_SUFFIX}")
            self.ctx = self.browser.new_context(viewport=VIEWPORT, device_scale_factor=2, locale="vi-VN",
                                                java_script_enabled=True, accept_downloads=False,
                                                service_workers="block", user_agent=self.ua)
            self.ctx.set_default_timeout(PAGE_TIMEOUT * 1000)
            self.ctx.route("**/*", self._route)
            self.page = self.ctx.new_page()
        except BaseException:
            self._pw.stop()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def note(self, msg: str) -> None:
        if msg not in self.blocked:
            self.blocked.append(msg)

    def allowed(self, url: str, main: bool) -> bool:
        s = urlsplit(url)
        key = f"{s.scheme}://{s.netloc}"
        if key not in self._hosts:
            try:
                check_url(url)
                self._hosts[key] = True
            except Blocked as e:
                self._hosts[key] = False
                self.note(str(e))
        if not self._hosts[key]:
            return False
        if main:
            host = host_of(url)
            if not any(domain_match(host, d) for d in self.sites):
                self.note(f"không rời khỏi trang đã cho: {url[:120]}")
                return False
            ok, note = self.robots.check(url)
            if not ok:
                self.note(note)
                return False
        return True

    def _route(self, route: Any) -> None:
        req = route.request
        url = req.url
        try:
            if not url.startswith(("http://", "https://")):
                return route.continue_()  # data:, blob:, about: — no network
            if req.resource_type in ("media", "websocket", "eventsource", "manifest"):
                return route.abort()
            nav = req.is_navigation_request()
            main = nav and req.frame.parent_frame is None
            for _ in range(MAX_REDIRECTS + 1):
                if not self.allowed(url, main):
                    return route.abort("blockedbyclient")
                r = route.fetch(url=url, max_redirects=0, timeout=PAGE_TIMEOUT * 1000)
                loc = r.headers.get("location")
                if r.status in REDIRECT and loc:
                    url = urljoin(url, loc)
                    if nav:
                        if not self.allowed(url, main):
                            return route.abort("blockedbyclient")
                        if main:
                            self.stub = url
                        return route.fulfill(status=200, content_type="text/html",
                                             body=f"<script>location.replace({json.dumps(url)})</script>")
                    continue
                if main:
                    self.status = r.status
                return route.fulfill(response=r)
            self.note(f"quá nhiều chuyển hướng: {url[:120]}")
            return route.abort()
        except Exception:  # noqa: BLE001 — page closed, network error: the request fails, the capture goes on
            try:
                route.abort()
            except Exception:  # noqa: BLE001
                pass

    def open(self, url: str, timeout: float = PAGE_TIMEOUT) -> int | None:
        """Navigate and follow our client-side redirects; the final document's HTTP status (None if refused)."""
        self.stub, self.status = None, None
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        for _ in range(MAX_REDIRECTS):
            if not self.stub:
                break
            target, self.stub = self.stub, None
            self.page.wait_for_url(target, wait_until="domcontentloaded", timeout=timeout * 1000)
        return self.status

    def js(self) -> None:
        if not self.page.evaluate("!!window.__mf"):
            self.page.evaluate(_page_js())

    def settle(self, scroll_steps: int = 15) -> str | None:
        """Close what covers the page and load lazy images: Escape, one consent button, scroll down and back up."""
        for state, ms in (("load", 8000), ("networkidle", 3000)):
            try:
                self.page.wait_for_load_state(state, timeout=ms)
            except Exception:  # noqa: BLE001 — a page that keeps polling is still usable
                pass
        self.js()
        try:
            self.page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        clicked = self.page.evaluate("__mf.dismiss()")
        if clicked:
            self.page.wait_for_timeout(500)
        self.js()
        self.page.evaluate(f"__mf.scrollThrough({scroll_steps})")
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001
            pass
        return clicked

    def call(self, expr: str, *args: Any) -> Any:
        self.js()
        return self.page.evaluate(expr, *args) if args else self.page.evaluate(expr)

    def screenshot(self, selector: str | None = None, *, omit_background: bool = False) -> bytes | None:
        self.call("__mf.hideOverlays()")
        try:
            if selector:
                return self.page.locator(selector).first.screenshot(type="png", omit_background=omit_background,
                                                                    timeout=8000)
            return self.page.screenshot(type="png")
        except Exception:  # noqa: BLE001 — element gone or too large
            return None

    def rasterise_svg(self, data: bytes, width: int = 512) -> bytes | None:
        """An SVG logo drawn by the same Chromium (transparent background)."""
        import base64

        p = self.ctx.new_page()
        try:
            src = "data:image/svg+xml;base64," + base64.b64encode(data).decode()
            p.set_content(f'<body style="margin:0;background:transparent"><img id="l" src="{src}" style="width:{width}px"></body>')
            p.wait_for_function("document.getElementById('l').complete", timeout=5000)
            return p.locator("#l").screenshot(type="png", omit_background=True)
        except Exception:  # noqa: BLE001
            return None
        finally:
            p.close()


_JS: str | None = None


def _page_js() -> str:
    global _JS
    if _JS is None:
        _JS = Path(__file__).with_name("webcapture.js").read_text("utf-8")
    return _JS


# ------------------------------------------------------------------ capture (A) and its checklist score (§12.1)

CHECK_LABELS = {"name": "tên sản phẩm hoặc trang", "sentence": "câu mô tả", "price": "giá hoặc con số",
                "pictures": "ảnh sản phẩm (ít nhất 2 ảnh lớn)", "logo": "logo", "clean": "trang mở không lỗi"}
PICTURE_KINDS = ("product", "image", "block")  # what counts as a product picture in the checklist


def checklist(cap: dict[str, Any], want: dict[str, Any] | None = None) -> tuple[int, list[str]]:
    """Deterministic 0–100 score and the keys still missing (CHECK_LABELS)."""
    want = want or {}
    facts = cap.get("facts") or []
    keys = {f["key"] for f in facts}
    score, missing = 0, []
    if "name" in keys:
        score += 20
    else:
        missing.append("name")
    if any(f["key"] in ("description", "heading") and len(f["value"].split()) >= 5 for f in facts):
        score += 15
    else:
        missing.append("sentence")
    if not want.get("offer") or "price" in keys:
        score += 15
    else:
        missing.append("price")
    big = [p for p in cap.get("pictures") or [] if p["kind"] in PICTURE_KINDS and min(p["w"], p["h"]) >= MIN_SIDE]
    score += 25 if len(big) >= 2 else 12 if big else 0
    if len(big) < 2:
        missing.append("pictures")
    if cap.get("logo"):
        score += 15
    else:
        missing.append("logo")
    if not cap.get("blocking"):
        score += 10
    else:
        missing.append("clean")
    return score, missing


def _same_image(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return pa.netloc.lower().removeprefix("www.") == pb.netloc.lower().removeprefix("www.") and pa.path == pb.path


def _product_sel(scan: dict[str, Any], cands: list[dict[str, Any]]) -> str | None:
    """The <img> on the page that shows the declared product picture, else the largest visible picture."""
    for c in cands:
        if c["kind"] == "product":
            hit = next((im for im in scan.get("images") or [] if _same_image(im["src"], c["url"])), None)
            if hit:
                return hit["sel"]
    page = [c for c in cands if c["source"] == "page:img" and c.get("sel")]
    return page[0]["sel"] if page else None


def _downscale(png: bytes, dest: Path, width: int) -> Path | None:
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(png)) as im:
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))))
            im.save(dest, quality=72)
        return dest
    except Exception:  # noqa: BLE001
        return None


class Capture:
    """What one capture collected; turned into capture.json by `data()`."""

    def __init__(self, out: Path, want: dict[str, Any] | None):
        self.dir = out / "capture"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.want = want or {}
        self.facts: list[dict[str, Any]] = []
        self.pic_cands: list[dict[str, Any]] = []
        self.logo_cands: list[dict[str, Any]] = []
        self.shots: list[dict[str, Any]] = []
        self.pictures: list[dict[str, Any]] = []
        self.logo: dict[str, Any] | None = None
        self.hashes: list[int] = []
        self.warnings: list[str] = []
        self.blocking: list[str] = []
        self.pages: list[dict[str, Any]] = []
        self.preview: str | None = None
        self.n = 0
        self.total = 0

    def add_facts(self, facts: list[dict[str, Any]]) -> None:
        for f in facts:
            if not any(x["key"] == f["key"] and x["value"] == f["value"] for x in self.facts):
                self.facts.append(f)

    def keep(self, data: bytes, cand: dict[str, Any], *, min_side: int = MIN_SIDE) -> dict[str, Any] | None:
        """Save a picture if it is big enough and not a near-duplicate of one already kept."""
        self.n += 1
        r = save_picture(data, self.dir / f"{'s' if cand['kind'] in ('hero', 'block') else 'p'}{self.n}")
        if not r:
            return None
        path, w, h, hsh = r
        # two screenshots of one page share its layout: only an almost identical one is a duplicate
        if min(w, h) < min_side or near_duplicate(hsh, self.hashes, 2 if cand["kind"] in ("hero", "block") else 6):
            path.unlink(missing_ok=True)
            return None
        self.hashes.append(hsh)
        pic = {"file": str(path), "kind": cand["kind"], "w": w, "h": h, "source": cand["source"],
               "url": cand.get("url") or "", "page": cand.get("page") or "", "alt": _nfc(cand.get("alt"))[:200],
               **({"sel": cand["sel"]} if cand.get("sel") else {})}
        self.pictures.append(pic)
        return pic

    def data(self, urls: list[str], robots: dict[str, str]) -> dict[str, Any]:
        pics = sorted(self.pictures, key=lambda p: RANK.get(p["kind"], 9))  # stable: page order within a kind
        for i, p in enumerate(pics):
            p["recommended"] = i < RECOMMENDED
        cap = {"urls": urls, "captured_at": int(time.time()), "robots": robots, "facts": self.facts,
               "pictures": pics, "logo": self.logo, "preview": self.preview, "pages": self.pages,
               "warnings": list(dict.fromkeys(self.warnings)), "blocking": list(dict.fromkeys(self.blocking))}
        score, missing = checklist(cap, self.want)
        cap["confidence"] = {"checklist": score, "score": score, "missing": missing}
        return cap


def normalise_urls(urls: list[Any]) -> tuple[list[str], list[str]]:
    """(usable URLs, warnings): at most 3, https:// added to a bare domain, each through the guard."""
    ok, warns = [], []
    for u in [str(x).strip() for x in urls or [] if str(x or "").strip()][:MAX_URLS]:
        if "://" not in u:
            u = "https://" + u
        try:
            ok.append(check_url(u))
        except Blocked as e:
            warns.append(str(e))
    return list(dict.fromkeys(ok)), warns


def visit(s: Session, c: Capture, url: str, *, screenshots: bool, first: bool) -> dict[str, Any] | None:
    """Open one page and collect its facts, picture and logo candidates and screenshots; the page's scan."""
    deadline = time.monotonic() + URL_BUDGET
    ok, note = s.robots.check(url)
    if not ok:
        c.warnings.append(f"robots.txt không cho phép {url[:120]} ({note})")
        c.blocking.append("robots")
        return None
    try:
        status = s.open(url)
    except Exception as e:  # noqa: BLE001 — timeout, refused navigation, DNS
        c.warnings.append(f"không mở được {url[:120]}: {type(e).__name__}: {str(e)[:120]}")
        c.blocking.append("http")
        return None
    if status is None or status >= 400:
        c.warnings.append(f"trang trả về HTTP {status}: {url[:120]}")
        c.blocking.append("http")
        if status is None:
            return None
    consent = s.settle()
    scan = s.call("__mf.scan()")
    c.pages.append({"url": scan["url"], "status": status, "title": scan["title"], **({"consent": consent} if consent else {})})
    facts, pics, logos = facts_from(scan)
    c.add_facts(facts)
    c.pic_cands += [{**p, "page": scan["url"]} for p in pics]
    c.logo_cands += [{**lg, "page": scan["url"]} for lg in logos]
    for sv in (scan.get("svgLogos") or [])[:1]:
        png = s.screenshot(sv["sel"], omit_background=True)
        if png:
            c.logo_cands.append({"data": png, "source": "header:svg", "sel": sv["sel"], "page": scan["url"]})
    if screenshots and time.monotonic() < deadline:
        sel = _product_sel(scan, pics)
        texts = [f["value"] for f in facts if f["key"] in ("price", "name")][:6]
        if sel and texts and s.call("([s, t]) => __mf.productBlock(s, t)", [sel, texts]):
            png = s.screenshot("[data-mf-block]")
            if png:
                c.shots.append({"data": png, "kind": "block", "source": "screenshot:block", "url": scan["url"],
                                "page": scan["url"], "sel": sel})
        png = s.screenshot()
        if png:
            c.shots.append({"data": png, "kind": "hero", "source": "screenshot:hero", "url": scan["url"], "page": scan["url"]})
        if first and time.monotonic() < deadline:
            try:
                full = s.page.screenshot(full_page=True, type="jpeg", quality=70)
                c.preview = str(_downscale(full, c.dir / "preview.jpg", 720) or "") or None
            except Exception:  # noqa: BLE001 — very tall pages
                pass
    return scan


def collect(s: Session, c: Capture) -> None:
    """Download the picture and logo candidates through the guard; keep the screenshots; rank."""
    import httpx

    client = httpx.Client(timeout=PAGE_TIMEOUT, follow_redirects=False, headers={"User-Agent": s.ua})
    seen: set[str] = set()
    try:
        for cand in c.pic_cands:
            if len(c.pictures) >= MAX_PICTURES or c.total >= MAX_TOTAL:
                break
            key = cand["url"].split("#")[0]
            if key in seen or not key.startswith(("http://", "https://")):
                continue
            seen.add(key)
            try:
                status, body, ctype, final = http_get(key, max_bytes=min(MAX_PICTURE, MAX_TOTAL - c.total),
                                                      accept=("image/",), client=client,
                                                      headers={"Referer": cand.get("page") or key})
            except (Blocked, httpx.HTTPError) as e:
                c.warnings.append(f"bỏ ảnh {key[:100]}: {e}"[:200])
                continue
            c.total += len(body)
            if status < 300 and ctype != "image/svg+xml":
                c.keep(body, {**cand, "url": final})
        for shot in c.shots:
            if len(c.pictures) < MAX_PICTURES + 2:
                c.keep(shot["data"], shot)
        for cand in c.logo_cands:
            data = cand.get("data")
            if data is None:
                try:
                    status, data, ctype, final = http_get(cand["url"], max_bytes=2 * 1024 * 1024, accept=("image/",),
                                                          client=client, headers={"Referer": cand.get("page") or ""})
                except (Blocked, httpx.HTTPError):
                    continue
                if status >= 300:
                    continue
                if ctype == "image/svg+xml":
                    data = s.rasterise_svg(data)
                    if not data:
                        continue
                cand = {**cand, "url": final}
            r = save_picture(data, c.dir / "logo")
            if r and max(r[1], r[2]) >= MIN_LOGO:
                c.logo = {"file": str(r[0]), "w": r[1], "h": r[2], "source": cand["source"],
                          "url": cand.get("url") or "", "page": cand.get("page") or "",
                          **({"sel": cand["sel"]} if cand.get("sel") else {})}
                break
            if r:
                r[0].unlink(missing_ok=True)
    finally:
        client.close()


Progress = Callable[[float, str], None]


def capture(urls: list[Any], out: Path, *, screenshots: bool = True, want: dict[str, Any] | None = None,
            progress: Progress | None = None, improve: bool = True) -> dict[str, Any]:
    """Visit the owner's pages (≤ 3) and write <out>/capture.json + <out>/capture/. want: {"offer": bool} — whether
    the video needs a price. improve: below 90 % confidence, try the site reference and the agent (§12.1)."""
    progress = progress or (lambda p, m: None)
    clean, warns = normalise_urls(urls)
    if not clean:
        raise Blocked("không có địa chỉ web nào dùng được: " + "; ".join(warns or ["trống"]))
    c = Capture(out, want)
    c.warnings += warns
    robots = Robots(robots_fetch)
    sites = list(dict.fromkeys(site_of(host_of(u)) for u in clean))
    scans: dict[str, dict[str, Any]] = {}
    with Session(sites, robots) as s:
        for i, u in enumerate(clean):
            progress(0.05 + 0.5 * i / len(clean), f"đang mở {host_of(u)}")
            scan = visit(s, c, u, screenshots=screenshots, first=i == 0)
            if scan:
                scans[scan["url"]] = scan
        progress(0.6, "đang tải ảnh")
        collect(s, c)
        c.warnings += [f"đã chặn: {b}" for b in s.blocked]
        cap = c.data(clean, dict(robots.notes))
        if improve:
            cap = improve_capture(s, c, cap, clean, scans, progress)
    (out / "capture.json").write_text(json.dumps(cap, ensure_ascii=False, indent=2))
    progress(1.0, "đã lấy xong")
    return cap


def improve_capture(s: Session, c: Capture, cap: dict[str, Any], urls: list[str], scans: dict[str, dict[str, Any]],
                    progress: Progress) -> dict[str, Any]:
    """§12.1: LLM score, site reference, guarded agent. (Step 3.)"""
    return cap
