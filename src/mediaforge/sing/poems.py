"""Built-in Tang poems (thơ Đường) for the Ngâm thơ tab.

poems_catalog.json holds the original (chữ Hán), the Hán-Việt reading (phiên âm, public domain) and, where the
translator died more than 50 years ago, a Vietnamese verse translation. Every text was copied from a fetched
source page (see source_urls), never typed from memory.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).with_name("poems_catalog.json")


@cache
def catalog() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(CATALOG.read_text("utf-8"))) if CATALOG.exists() else ()


def get(poem_id: str) -> dict[str, Any]:
    for p in catalog():
        if p["id"] == poem_id:
            return p
    raise KeyError(f"unknown poem {poem_id!r}")


def versions(poem: dict[str, Any]) -> list[str]:
    """'phien-am' plus one entry per verse translation ('dich:<translator>')."""
    return ["phien-am"] + [f"dich:{t['translator']}" for t in poem.get("translations", [])]


def text_for(poem: dict[str, Any], version: str | None = None) -> str:
    """The lines to chant. Default: the first verse translation if any (Vietnamese listeners understand it),
    else the Hán-Việt reading."""
    trs = poem.get("translations") or []
    if version in (None, "", "auto"):
        version = f"dich:{trs[0]['translator']}" if trs else "phien-am"
    if version == "phien-am":
        return "\n".join(poem["phien_am"])
    for t in trs:
        if version == f"dich:{t['translator']}":
            return "\n".join(t["lines"])
    raise KeyError(f"poem {poem['id']!r} has no version {version!r}; choose from {versions(poem)}")


def summary() -> list[dict[str, Any]]:
    return [{"id": p["id"], "title": p["title_hv"], "title_han": p.get("title_han"), "author": p["author_hv"],
             "form": p.get("form"), "mood": p.get("mood"), "versions": versions(p),
             "phien_am": p["phien_am"], "translations": p.get("translations", []), "dich_nghia": p.get("dich_nghia")}
            for p in catalog()]
