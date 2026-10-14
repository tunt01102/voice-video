"""Background-music library: a curated catalogue of free, publicly usable tracks.

Metadata (title, licence, credit, source) ships in music_catalog.json; the audio itself is downloaded once into
data/music/ by `mediaforge music sync`. A video's `music` param may be a catalogue id or a local file path.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from mediaforge.config import settings

log = logging.getLogger(__name__)
CATALOG = Path(__file__).with_name("music_catalog.json")


@cache
def catalog() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(CATALOG.read_text("utf-8")))


def _track(track_id: str) -> dict[str, Any] | None:
    return next((t for t in catalog() if t["id"] == track_id), None)


def track_path(t: dict[str, Any]) -> Path:
    # Everything is stored as mp3 so the dashboard preview plays in any browser.
    return settings.sub("music") / f"{t['id']}.mp3"


def list_tracks() -> list[dict[str, Any]]:
    return [{**t, "available": track_path(t).exists()} for t in catalog()]


def resolve(music: str | None) -> str | None:
    """Catalogue id → local file; anything else is taken as a path."""
    if not music:
        return None
    t = _track(music)
    if t is None:
        return music
    f = track_path(t)
    if not f.exists():
        raise FileNotFoundError(f"music track {music!r} is not downloaded yet — run `mediaforge music sync`")
    return str(f)


def credit(music: str | None) -> str | None:
    """Attribution line the video must carry (CC BY tracks), else None."""
    t = _track(music) if music else None
    return t.get("attribution") if t else None


class RateLimited(Exception):
    pass


UA = "MediaForge/0.1 (local music library sync)"


def _fetch(url: str, dest: Path) -> None:
    # curl, not httpx: Wikimedia's edge throttles httpx clients (HTTP 429) while serving curl normally.
    for _ in range(5):
        r = subprocess.run(["curl", "-sSL", "--max-time", "300", "-A", UA, "-o", str(dest), "-w", "%{http_code} %{content_type}",
                            "-D", "-", url], capture_output=True, text=True)
        if r.returncode:
            raise OSError(r.stderr.strip() or f"curl exit {r.returncode}")
        code, _, ctype = r.stdout.rsplit("\n", 1)[-1].partition(" ")
        if code == "429":
            # Short pauses are waited out; a long one means "come back later" (re-run sync).
            m = re.search(r"(?im)^retry-after:\s*(\d+)", r.stdout)
            wait = int(m[1]) if m else 600
            if wait > 60:
                raise RateLimited(f"rate-limited by {urlsplit(url).hostname} (retry after {wait}s)")
            time.sleep(wait + 1)
            continue
        if code != "200":
            raise OSError(f"HTTP {code}")
        if not (ctype.startswith("audio/") or ctype in ("application/ogg", "application/octet-stream")):
            raise ValueError(f"not audio ({ctype})")
        return
    raise RateLimited(f"still rate-limited by {urlsplit(url).hostname}")


def sync(progress: Callable[[str], None] = log.info) -> dict[str, list[str]]:
    """Download every catalogue track that is missing (as mp3). Safe to re-run."""
    out: dict[str, list[str]] = {"downloaded": [], "present": [], "failed": []}
    throttled: set[str] = set()
    for t in catalog():
        dest = track_path(t)
        if dest.exists():
            out["present"].append(t["id"])
            continue
        host = urlsplit(t["download_url"]).hostname or ""
        if host in throttled:
            out["failed"].append(t["id"])
            continue
        src = dest.with_name(f"{t['id']}.src.{t['ext']}")
        part = dest.with_suffix(".part.mp3")
        try:
            _fetch(t["download_url"], src)
            if t["ext"] == "mp3":
                src.rename(part)
            else:
                subprocess.run([settings.ffmpeg, "-y", "-v", "error", "-i", str(src), "-vn", "-c:a", "libmp3lame",
                                "-b:a", "192k", str(part)], check=True)
            part.rename(dest)
            out["downloaded"].append(t["id"])
            progress(f"downloaded {t['id']}")
            time.sleep(3)  # be polite to Wikimedia
        except RateLimited as e:
            throttled.add(host)
            out["failed"].append(t["id"])
            progress(f"failed {t['id']}: {e}")
        except (ValueError, OSError, subprocess.CalledProcessError) as e:
            out["failed"].append(t["id"])
            progress(f"failed {t['id']}: {e}")
        finally:
            src.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
    return out
