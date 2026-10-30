"""Web capture guard (docs/SDD-web-capture.md §4, §9, §10.2): SSRF checks, robots.txt per RFC 9309, own sites."""

import json

import httpx
import pytest

from mediaforge import webcapture as W
from mediaforge.config import settings

# The three own sites' robots.txt as fetched on 2026-10-31 (trimmed).
EASYVISA = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Disallow: /*?chat=
Disallow: /*?conversation=
Disallow: /*?takeover=

Sitemap: https://easyvisarun.com/sitemap.xml
"""
TRADESWOMAN = """User-Agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /checkout
Disallow: /order/
Disallow: /research

Host: https://tradeswomanmerch.com
"""
AISME = """# AI for SME
User-agent: *
Allow: /
Disallow: /api/

User-agent: OAI-SearchBot
User-agent: ChatGPT-User
Allow: /
"""


@pytest.fixture
def dns(monkeypatch):
    table: dict[str, list[str]] = {}
    monkeypatch.setattr(W, "resolve", lambda host: table[host])
    return table


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254",
                                  "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "ff02::1",
                                  "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::"])
def test_private_addresses_are_refused(addr):
    assert not W.public_ip(addr)


@pytest.mark.parametrize("addr", ["45.252.248.13", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_pass(addr):
    assert W.public_ip(addr)


def test_check_url_schemes_credentials_and_dns(dns):
    dns.update({"shop.vn": ["45.252.248.13"], "evil.test": ["45.252.248.13", "127.0.0.1"],
                "meta.test": ["169.254.169.254"]})
    assert W.check_url("https://shop.vn/a") == "https://shop.vn/a"
    for bad in ("file:///etc/passwd", "ftp://shop.vn/", "javascript:alert(1)", "https://user:pw@shop.vn/",
                "https:///nohost", "http://evil.test/", "http://meta.test/latest/meta-data/"):
        with pytest.raises(W.Blocked):
            W.check_url(bad)


def test_ip_literals_and_the_test_override(monkeypatch):
    for bad in ("http://127.0.0.1:8765/api/health", "http://[::1]/", "http://2130706433/", "http://0x7f.1/"):
        with pytest.raises(W.Blocked):
            W.check_url(bad)
    monkeypatch.setattr(settings, "web_capture_allow_hosts", {"127.0.0.1"})
    assert W.check_url("http://127.0.0.1:9/x")


def test_redirect_to_a_private_address_is_refused(dns):
    dns.update({"shop.vn": ["45.252.248.13"], "internal.test": ["10.0.0.5"]})
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "shop.vn":
            return httpx.Response(302, headers={"location": "http://internal.test/secret"})
        return httpx.Response(200, content=b"secret")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(W.Blocked, match="internal.test"):
        W.http_get("https://shop.vn/img.jpg", max_bytes=1000, client=client)
    assert seen == ["https://shop.vn/img.jpg"]  # the private hop was never requested


def test_http_get_limits_type_and_size(dns):
    dns["shop.vn"] = ["45.252.248.13"]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/page":
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "image/jpeg"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(W.Blocked, match="ảnh"):
        W.http_get("https://shop.vn/page", max_bytes=10_000, accept=("image/",), client=client)
    with pytest.raises(W.Blocked, match="lớn"):
        W.http_get("https://shop.vn/big.jpg", max_bytes=1000, accept=("image/",), client=client)
    status, body, ctype, final = W.http_get("https://shop.vn/ok.jpg", max_bytes=10_000, accept=("image/",), client=client)
    assert (status, len(body), ctype, final) == (200, 5000, "image/jpeg", "https://shop.vn/ok.jpg")


def test_robots_rfc9309_wildcards_and_longest_match():
    rules = W.parse_robots(EASYVISA)
    assert W.robots_allows(rules, "/")
    assert W.robots_allows(rules, "/visa/korea")
    assert not W.robots_allows(rules, "/admin/users")
    assert not W.robots_allows(rules, "/visa?chat=1")  # `*` wildcard: urllib.robotparser would allow this
    assert W.robots_allows(rules, "/robots.txt")
    rules = W.parse_robots(TRADESWOMAN)
    assert not W.robots_allows(rules, "/checkout/step-2") and W.robots_allows(rules, "/shop/caps")
    rules = W.parse_robots("User-agent: *\nDisallow: /shop\nAllow: /shop/public\nDisallow: /*.pdf$\n")
    assert W.robots_allows(rules, "/shop/public/a")  # longer Allow beats shorter Disallow
    assert not W.robots_allows(rules, "/shop/private")
    assert not W.robots_allows(rules, "/files/a.pdf") and W.robots_allows(rules, "/files/a.pdf?x=1")
    tie = W.parse_robots("User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert W.robots_allows(tie, "/page")  # Allow wins a tie


def test_robots_groups_pick_our_token_over_star():
    txt = "User-agent: *\nDisallow: /\n\nUser-agent: mediaforge\nUser-agent: other\nDisallow: /private\n"
    rules = W.parse_robots(txt)
    assert W.robots_allows(rules, "/shop") and not W.robots_allows(rules, "/private/x")
    assert W.robots_allows(W.parse_robots(AISME), "/blog") and not W.robots_allows(W.parse_robots(AISME), "/api/x")
    assert W.robots_allows(W.parse_robots("User-agent: *\nDisallow:\n"), "/anything")  # empty Disallow


def test_robots_status_rules():
    assert W.robots_rules("https://a.vn", lambda u: (404, b""))[0] == []  # 4xx: no rules
    assert W.robots_rules("https://a.vn", lambda u: (503, b""))[0] is None  # 5xx: complete disallow

    def down(u):
        raise httpx.ConnectError("refused")

    assert W.robots_rules("https://a.vn", down)[0] is None


def test_robots_own_sites_are_exempt_and_cached():
    calls = []

    def fetch(u):
        calls.append(u)
        return 200, b"User-agent: *\nDisallow: /\n"

    r = W.Robots(fetch, own=["aisme.vn"])
    assert r.check("https://www.aisme.vn/san-pham")[0]  # own site, subdomain included
    assert not r.check("https://other.vn/a")[0] and not r.check("https://other.vn/b")[0]
    assert calls == ["https://other.vn/robots.txt"]  # one fetch per origin, none for the own site
    assert "cấm" in r.notes["other.vn"]


def test_own_sites_seed_clean_and_save():
    assert W.own_sites() == list(W.OWN_SITES_SEED)
    saved = W.save_own_sites(["https://www.Shop.VN/path", "shop.vn", "not a domain", "", "cửahàng.vn", "x"])
    assert saved == ["shop.vn", "cửahàng.vn".encode("idna").decode()]
    assert W.own_sites() == saved
    assert W.is_own("m.shop.vn") and not W.is_own("myshop.vn")
    W.save_own_sites(list(W.OWN_SITES_SEED))


def test_price_pattern_needs_the_currency_word_to_end():
    for ok in ("45.000đ", "Giá: 45.000 ₫", "$34.00", "25 USD", "1.900.000 VND"):
        assert W.PRICE.search(ok), ok
    for no in ("9 đánh giá thật", "3 đêm", "100% đúng", "2026"):
        assert not W.PRICE.search(no), no


# Facts from the easyvisarun.com and tradeswomanmerch.com captures (E2E 2026-10-31).
SITE_FACTS = ["1900000 VND", "1.900.000 VND", "7,6 triệu đồng/xe cho 2–5 khách", "Khoảng 1,31 triệu đồng (50 USD)",
              "Một lần 25 USD (≈ 650.000đ)", "1 USD ≈ 26.170 ₫", "$34.00", "Visa run Đà Nẵng 2026"]


@pytest.mark.parametrize("said", [
    "một triệu chín trăm nghìn đồng", "chỉ một triệu chín", "1.900.000đ", "xe riêng bảy phẩy sáu triệu",
    "7.600.000 đồng", "một phẩy ba mốt triệu", "khoảng một phẩy ba mươi mốt triệu", "sáu trăm năm mươi nghìn",
    "hai mươi lăm đô", "năm hai nghìn không trăm hai mươi sáu", "thirty-four dollars", "34 dollars",
    "one million nine hundred thousand dong", "fifty dollars", "ba bước, năm câu hỏi, một chuyến đi",
    "one of the best, two steps"])
def test_spoken_numbers_match_the_site(said):
    assert W.unsourced(said, W.number_set(SITE_FACTS)) == [], said


@pytest.mark.parametrize(("said", "flagged"), [
    ("chỉ chín trăm nghìn đồng", ["chín trăm nghìn"]), ("hai triệu rưỡi", ["hai triệu rưỡi"]),
    ("hơn mười năm kinh nghiệm", ["mười"]), ("hai mươi năm", ["hai mươi"]), ("một trăm phần trăm", ["một trăm"]),
    ("một trăm linh năm khách", ["một trăm linh năm"]), ("twenty-six dollars", ["twenty-six"]),
    ("two point five million", ["two point five million"]), ("only 1.800.000đ", ["1.800.000"]),
    ("năm 2025", ["2025"])])
def test_spoken_numbers_not_on_the_site_are_flagged(said, flagged):
    assert W.unsourced(said, W.number_set(SITE_FACTS)) == flagged


def test_number_readings():
    assert W.number_set(["45.000đ"]) == W.number_set(["45000 VND"]) == {"45000"}
    assert W.number_set(["$34.00"]) == {"34", "3400"} and W.number_set(["26.170 ₫"]) == {"26170"}
    assert W.number_set(["7,6 triệu"]) == {"7600000", "76000000"} and W.number_set(["45k"]) == {"45000"}
    assert W.number_set(["năm 2026, trăm năm, Năm câu hỏi"]) == {"2026"}  # never 52026, never 105
    assert W.number_set(["hai nghìn không trăm hai mươi sáu", "ba mốt", "mười tư", "a hundred and five"]) == {
        "2026", "31", "14", "105"}


def test_gate_ticks_twenty_facts_without_repeats(tmp_path):
    page = "https://shop.vn/"
    facts = [{"key": "price", "value": "1900000 VND", "source": "jsonld:Offer", "url": page},
             {"key": "name", "value": "Visa run", "source": "og:title", "url": page},
             {"key": "name", "value": "Visa run | EasyVisa", "source": "meta:title", "url": page},
             {"key": "heading", "value": "Visa run", "source": "text:h2", "url": page},
             {"key": "price", "value": "650.000 VND", "source": "text:price", "url": page},
             {"key": "price", "value": "+ E-visa 650.000 VND", "source": "text:price", "url": page},
             {"key": "price", "value": "1.900.000 VND", "source": "text:price", "url": page},
             {"key": "description", "value": "Dịch vụ trong ngày", "source": "og:description", "url": page}]
    facts += [{"key": "heading", "value": f"Mục {i}", "source": "text:h2", "url": page} for i in range(30)]
    facts.append({"key": "heading", "value": "Trang hai", "source": "text:h1", "url": page + "b"})
    W.recommend_facts(facts)
    ticked = [f["value"] for f in facts if f["recommended"]]
    assert len(ticked) == W.FACTS_SHOWN
    # one name per page, one line per price (the longer), the h1 of every page before the description and the h2s
    # an approval without edits keeps the ticked facts; an old capture (no marks) keeps all; more than 40 is refused
    def approve(facts):
        (tmp_path / "capture.json").write_text(json.dumps({"urls": [page], "pictures": [], "logo": None, "facts": facts}))
        return [f["value"] for f in W.apply_approval(tmp_path, None)["kept"]["facts"]]

    assert approve(facts) == ticked
    old = [{k: v for k, v in f.items() if k != "recommended"} for f in facts]
    assert len(approve(old)) == len(old) == 39
    with pytest.raises(ValueError, match="tối đa 40"):
        approve(old + old[-2:])
    # (kept in page order)
    assert ticked == ["Visa run", "+ E-visa 650.000 VND", "1.900.000 VND", "Dịch vụ trong ngày",
                      *[f"Mục {i}" for i in range(15)], "Trang hai"]
