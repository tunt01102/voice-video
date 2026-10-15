"""Animated slides: one HTML scene per slide (anime.js timelines, three.js for the `3d` layout), stepped
frame by frame in headless Chromium and piped into FFmpeg — a licence-clean stand-in for Remotion
(docs/SKILLS-REVIEW.md). The page never reads the clock: `__seek(t)` alone decides a frame, so a render
is reproducible and exactly `seconds` long.
"""

from __future__ import annotations

import logging
import mimetypes
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mediaforge.config import settings
from mediaforge.video import slides

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "scene_web"
ORIGIN = "http://mediaforge.scene"  # served from WEB via request routing; nothing listens on the network
LAYOUTS = ("bullets", "stat", "3d")
SHAPES = ("globe", "cube", "torus", "network")


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return (WEB / "scene.html").is_file()


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def colors(theme: str) -> dict[str, str]:
    t = slides.THEMES.get(theme, slides.THEMES["midnight"])
    return {"bg1": _hex(t["bg"][0]), "bg2": _hex(t["bg"][1]), "title": _hex(t["title"]),
            "text": _hex(t["text"]), "accent": _hex(t["accent"])}


def layout_of(sc: dict[str, Any], index: int) -> str:
    """The plan's layout when valid; otherwise a stat slide when it has a stat, else bullets (3d opens the deck)."""
    lay = sc.get("layout")
    if lay in LAYOUTS and (lay != "stat" or sc.get("stat")):
        return lay
    if sc.get("stat") and isinstance(sc["stat"], dict) and sc["stat"].get("value"):
        return "stat"
    return "3d" if index == 0 else "bullets"


class Renderer:
    """One Chromium for a whole video; `scene()` renders one slide to an MP4."""

    def __init__(self, page: Any, width: int, height: int):
        self.page, self.width, self.height = page, width, height

    def scene(self, out: Path, *, title: str, bullets: list[str], seconds: float, theme: str = "midnight",
              layout: str = "bullets", shape: str = "globe", stat: dict | None = None, background: str | None = None,
              counter: str = "", footer: str = "", progress: tuple[float, float] = (0.0, 0.0), fps: int = 30) -> Path:
        self._background = background if background and Path(background).is_file() else None
        self.page.goto(f"{ORIGIN}/scene.html")
        self.page.wait_for_function("window.__ready === true")
        data = {"title": title, "bullets": bullets[:5], "seconds": seconds, "width": self.width, "height": self.height,
                "colors": colors(theme), "layout": layout if layout in LAYOUTS else "bullets",
                "shape": shape if shape in SHAPES else "globe", "stat": stat, "background": bool(self._background),
                "counter": counter, "footer": footer, "progress0": progress[0], "progress1": progress[1]}
        self.page.evaluate("d => window.__setup(d)", data)
        frames = max(int(round(seconds * fps)), 2)
        out.parent.mkdir(parents=True, exist_ok=True)
        enc = subprocess.Popen(
            [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg",
             "-framerate", str(fps), "-i", "-", "-frames:v", str(frames), "-an", "-c:v", "h264_videotoolbox",
             "-b:v", "16M", "-pix_fmt", "yuv420p", str(out)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for k in range(frames):
                self.page.evaluate("t => window.__seek(t)", k / fps)
                enc.stdin.write(self.page.screenshot(type="jpeg", quality=92))
        finally:
            enc.stdin.close()
            err = enc.stderr.read().decode(errors="replace")
            enc.wait()
        if enc.returncode != 0:
            raise RuntimeError(f"ffmpeg (animated slide) failed: {err[-1500:]}")
        return out

    def _serve(self, route: Any) -> None:
        path = route.request.url.removeprefix(ORIGIN).split("?")[0]
        if path == "/bg" and self._background:
            f = Path(self._background)
        elif path.startswith("/font/"):
            f = slides.FONT_BOLD if path.endswith("bold.ttf") else slides.FONT_REG
        else:
            f = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() not in f.parents:
                return route.fulfill(status=404)
        if not f.is_file():
            return route.fulfill(status=404)
        ctype = "text/javascript" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        route.fulfill(status=200, body=f.read_bytes(), headers={"Content-Type": ctype})


@contextmanager
def renderer(width: int, height: int) -> Iterator[Renderer]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # ANGLE on Metal gives the headless shell hardware WebGL for the three.js layout.
        args = ["--use-angle=metal", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"]
        try:
            browser = pw.chromium.launch(args=args)
        except Exception:  # noqa: BLE001 — Playwright's own Chromium not downloaded: use installed Chrome
            browser = pw.chromium.launch(channel="chrome", args=args)
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            r = Renderer(page, width, height)
            r._background = None
            page.route("**/*", lambda route: r._serve(route) if route.request.url.startswith(ORIGIN) else route.abort())
            page.on("pageerror", lambda e: log.warning("animated slide JS error: %s", e))
            yield r
        finally:
            browser.close()
