"""Web capture (A) against a local fixture shop (docs/SDD-web-capture.md §9): JSON-LD, Open Graph only, a consent
banner, a covering pop-up, lazy and duplicate pictures, a tiny favicon, an SVG logo, robots.txt, and requests that
try to reach a private address — directly, by redirect, or as a sub-resource. Needs Playwright's Chromium."""

import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mediaforge import webcapture as W
from mediaforge.config import settings
from mediaforge.video import animate

FIXTURES = Path(__file__).parent / "fixtures" / "web"
pytestmark = pytest.mark.skipif(not animate.available(), reason="needs playwright")


def _images(root: Path) -> None:
    from PIL import Image, ImageDraw

    img = root / "img"
    img.mkdir()

    def pic(name: str, size: tuple[int, int], color: str, mark: int = 0, mode: str = "RGB") -> None:
        im = Image.new(mode, size, color)
        d = ImageDraw.Draw(im)
        d.rectangle([size[0] // 5 + mark * 40, size[1] // 5, size[0] // 2 + mark * 40, size[1] // 2], fill="white")
        d.ellipse([size[0] // 2, size[1] // 2, size[0] * 9 // 10, size[1] * 9 // 10], fill="black")
        im.save(img / name)

    pic("cb-1.jpg", (900, 900), "#7a4a2a")
    pic("cb-1-copy.jpg", (900, 900), "#7a4a2a")  # the same picture again: dropped as a duplicate
    pic("cb-2.jpg", (1200, 800), "#2a5a7a", mark=3)
    pic("lazy.jpg", (800, 600), "#5a7a2a", mark=6)
    pic("og.jpg", (1200, 630), "#aa3355", mark=1)
    pic("thumb.jpg", (200, 200), "#333333")  # too small
    pic("logo.png", (400, 120), (0, 0, 0, 0), mode="RGBA")
    pic("fav16.png", (16, 16), "#ff0000")


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    root = tmp_path_factory.mktemp("shop")
    for f in FIXTURES.iterdir():
        shutil.copy(f, root / f.name)
    _images(root)
    hits: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            port = self.server.server_address[1]
            if self.path in ("/go-out", "/img/redir.jpg"):
                target = {"/go-out": "/secret.html", "/img/redir.jpg": "/img/secret-redir.jpg"}[self.path]
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}{target}")  # a private address
                self.end_headers()
                return
            if self.path == "/product.html":
                body = (root / "product.html").read_text().replace("PORT", str(port)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


@pytest.fixture(autouse=True)
def allow_fixture_host(monkeypatch):
    monkeypatch.setattr(settings, "web_capture_allow_hosts", {"127.0.0.1"})


def test_product_page(shop, tmp_path):
    base, hits = shop
    cap = W.capture([base + "/product.html"], tmp_path, want={"offer": True}, improve=False)
    facts = {(f["key"], f["source"]): f["value"] for f in cap["facts"]}
    assert facts[("name", "jsonld:Product.name")] == "Cold brew Mộc"
    assert facts[("price", "jsonld:Product.offers")] == "45000 VND"
    assert facts[("price", "text:price")] == "45.000 ₫"
    assert facts[("brand", "jsonld:Product.brand")] == "Quán Mộc"
    assert ("heading", "text:h2") in facts
    pics = cap["pictures"]
    names = [Path(p["url"]).name for p in pics if p["kind"] != "hero" and p["kind"] != "block"]
    assert names[:2] == ["cb-1.jpg", "cb-2.jpg"] and pics[0]["kind"] == "product"
    assert "lazy.jpg" in names  # loaded by scrolling
    assert "cb-1-copy.jpg" not in names and "thumb.jpg" not in names  # duplicate, too small
    kinds = [p["kind"] for p in pics]
    assert "block" in kinds and "hero" in kinds
    assert kinds.index("block") < kinds.index("hero")
    assert [p["recommended"] for p in pics][:4] == [True] * 4 and not any(p["recommended"] for p in pics[4:])
    assert all(Path(p["file"]).is_file() and Path(p["file"]).parent == tmp_path / "capture" for p in pics)
    assert cap["logo"]["source"] == "jsonld:logo" and cap["logo"]["file"].endswith(".png")  # alpha kept
    assert cap["pages"][0]["consent"] == "đồng ý"
    assert cap["preview"] and Path(cap["preview"]).is_file()
    # the guard: no private address was ever requested — sub-resource, picture redirect, or link
    assert not any("secret" in h for h in hits)
    assert any("localhost" in w for w in cap["warnings"])
    assert cap["confidence"] == {"checklist": 100, "score": 100, "missing": []}
    saved = json.loads((tmp_path / "capture.json").read_text())
    assert saved["facts"] == cap["facts"]


def test_main_frame_redirect_to_a_private_address_is_refused(shop, tmp_path):
    base, hits = shop
    cap = W.capture([base + "/go-out"], tmp_path, improve=False)
    assert "/secret.html" not in hits
    assert "http" in cap["blocking"] and not cap["facts"]


def test_open_graph_only_and_a_tiny_favicon(shop, tmp_path):
    base, _ = shop
    cap = W.capture([base + "/og.html"], tmp_path, screenshots=False, want={"offer": True}, improve=False)
    assert {f["key"] for f in cap["facts"]} >= {"name", "description"}
    assert [p["source"] for p in cap["pictures"]] == ["og:image"]
    assert cap["logo"] is None  # a 16 px favicon is refused
    assert set(cap["confidence"]["missing"]) == {"price", "pictures", "logo"}
    assert cap["confidence"]["score"] == 20 + 15 + 12 + 10


def test_svg_logo_in_the_header(shop, tmp_path):
    base, _ = shop
    cap = W.capture([base + "/svglogo.html"], tmp_path, screenshots=False, improve=False)
    assert cap["logo"]["source"] == "header:svg" and cap["logo"]["w"] >= 128


def test_robots_disallow_is_obeyed(shop, tmp_path):
    base, hits = shop
    cap = W.capture([base + "/private.html"], tmp_path, improve=False)
    assert "/private.html" not in hits and "robots" in cap["blocking"]
    assert any("robots" in w for w in cap["warnings"])


def test_no_usable_url():
    with pytest.raises(W.Blocked):
        W.capture(["file:///etc/passwd", "javascript:1"], Path("/nonexistent"), improve=False)
