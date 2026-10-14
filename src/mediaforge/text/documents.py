"""Word/PDF import: ordered text with inline `[ảnh N]` markers + the document's images as PNGs.

CPU only (no model), so it runs in the request thread. docs/SDD-doc-import-preview-chibi.md §3.1.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

MIN_SIDE = 160        # smaller images are icons, bullets or logos
MAX_IMAGES = 40
MAX_CHARS = 60_000    # ≈ 4 h of narration; keeps the LLM prompt sane
CONVERT = {".doc", ".rtf", ".odt", ".rtfd", ".wordml"}  # via macOS textutil → .docx
SUPPORTED = {".docx", ".pdf", *CONVERT}

END = (".", "!", "?", "…", ":", ";", ",", '"', "”", ")")  # already ends a phrase: no full stop added
MARKER_RE = re.compile(r"\[\s*(?:ảnh|anh|image|img|hình|hinh)\s*(\d+)\s*\]", re.IGNORECASE)


def marker(n: int) -> str:
    return f"[ảnh {n}]"


class DocumentError(ValueError):
    pass


class _Collector:
    """Accumulates text blocks and images in document order."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.blocks: list[str] = []
        self.images: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._seen: set[str] = set()
        self._skipped_small = 0
        self._skipped_format = 0

    def text(self, s: str, *, close: bool = False) -> None:
        """close=True ends a heading/list line with a full stop so the narration pauses after it."""
        s = re.sub(r"[ \t\u00a0]+", " ", s).strip()
        if s and close and not s.endswith(END):
            s += "."
        if s:
            self.blocks.append(s)

    def image(self, data: bytes) -> None:
        digest = hashlib.sha1(data).hexdigest()
        if digest in self._seen:
            return
        self._seen.add(digest)
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:  # noqa: BLE001 — EMF/WMF/SVG and broken streams
            self._skipped_format += 1
            return
        if min(img.size) < MIN_SIDE:
            self._skipped_small += 1
            return
        if len(self.images) >= MAX_IMAGES:
            return
        n = len(self.images) + 1
        path = self.out_dir / f"image_{n:02d}.png"
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
        img.save(path)
        self.images.append({"path": str(path), "width": img.width, "height": img.height})
        self.blocks.append(marker(n))

    def result(self, title: str, kind: str) -> dict[str, Any]:
        text = "\n\n".join(self.blocks)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit("\n", 1)[0]
            self.warnings.append(f"Tài liệu dài, chỉ lấy {MAX_CHARS:,} ký tự đầu.")
            kept = {int(m) for m in MARKER_RE.findall(text)}
            self.images = [im for i, im in enumerate(self.images, 1) if i in kept]
        if self._skipped_format:
            self.warnings.append(f"Bỏ qua {self._skipped_format} ảnh định dạng không đọc được (EMF/WMF/SVG).")
        if self._skipped_small:
            self.warnings.append(f"Bỏ qua {self._skipped_small} ảnh nhỏ (icon/logo).")
        if len(self._seen) - self._skipped_format - self._skipped_small > MAX_IMAGES:
            self.warnings.append(f"Chỉ giữ {MAX_IMAGES} ảnh đầu tiên.")
        return {"title": title, "kind": kind, "text": text, "images": self.images, "warnings": self.warnings}


# ------------------------------------------------------------------ docx

def _docx(path: Path, c: _Collector) -> str:
    import docx
    from docx.oxml.ns import qn

    d = docx.Document(str(path))
    rels = d.part.related_parts
    title = (d.core_properties.title or "").strip()

    def paragraph(p) -> None:
        buf: list[str] = []
        # Walk runs in order so an inline picture lands between the words around it.
        for el in p._p.iter():
            if el.tag == qn("w:t") and el.text:
                buf.append(el.text)
            elif el.tag in (qn("w:tab"),):
                buf.append(" ")
            elif el.tag in (qn("w:br"), qn("w:cr")):
                buf.append("\n")
            elif el.tag == qn("a:blip"):
                rid = el.get(qn("r:embed"))
                part = rels.get(rid) if rid else None
                if part is not None and hasattr(part, "blob"):
                    c.text("".join(buf))
                    buf = []
                    c.image(part.blob)
        c.text("".join(buf), close=True)

    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            p = docx.text.paragraph.Paragraph(el, d)
            if not title and p.style is not None and p.style.name.lower() in ("title", "heading 1") and p.text.strip():
                title = p.text.strip()
            paragraph(p)
        elif el.tag == qn("w:tbl"):
            for row in docx.table.Table(el, d).rows:
                cells: list[str] = []
                for cell in row.cells:
                    t = " ".join(x.text.strip() for x in cell.paragraphs if x.text.strip())
                    if t and (not cells or cells[-1] != t):  # merged cells repeat
                        cells.append(t)
                    for p in cell.paragraphs:
                        for blip in p._p.iter(qn("a:blip")):
                            part = rels.get(blip.get(qn("r:embed")) or "")
                            if part is not None and hasattr(part, "blob"):
                                c.image(part.blob)
                if cells:
                    c.text(" — ".join(cells) + ("" if cells[-1][-1:] in ".!?…:" else "."))
    return title


# ------------------------------------------------------------------ pdf

def _pdf(path: Path, c: _Collector) -> str:
    from pypdf import PdfReader

    try:
        r = PdfReader(str(path))
        if r.is_encrypted:
            r.decrypt("")
    except Exception as e:  # noqa: BLE001
        raise DocumentError(f"Không mở được PDF: {e}") from e
    title = ((r.metadata or {}).get("/Title") or "").strip() if r.metadata else ""
    n_chars, n_pages_img = 0, 0
    for page in r.pages:
        try:
            raw = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001
            log.warning("pdf text failed on a page: %s", e)
            raw = ""
        n_chars += len(raw.strip())
        for para in re.split(r"\n\s*\n", _unwrap(raw)):
            c.text(para)
        try:
            imgs = list(page.images)
        except Exception as e:  # noqa: BLE001
            log.warning("pdf images failed on a page: %s", e)
            imgs = []
        n_pages_img += bool(imgs)
        for im in imgs:
            try:
                c.image(im.data if im.image is None else _png(im.image))
            except Exception:  # noqa: BLE001
                c.image(im.data)
    if re.search(r"\.\w{2,5}$", title) or title.lower().startswith(("untitled", "microsoft word")):
        title = ""  # a file name, not a title
    if n_chars < 20:
        raise DocumentError("PDF không có lớp chữ (có thể là bản scan) — chưa hỗ trợ OCR. "
                            "Hãy dùng PDF xuất từ Word hoặc dán nội dung vào ô Nội dung.")
    return title


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _unwrap(raw: str) -> str:
    """PDF text breaks at every visual line: re-join lines of one paragraph.

    Blank line → paragraph break; a bullet/numbered line starts a new paragraph; a line after a sentence end
    starts a new line; anything else continues the previous line (a trailing hyphen joins the word).
    A short line without end punctuation followed by a capitalised line ends its paragraph (a heading, or the
    last line of a paragraph) and gets a full stop so the narration pauses.
    """
    lines = [x.strip() for x in raw.splitlines()]
    full = max((len(x) for x in lines), default=0)
    paras: list[list[str]] = [[]]
    for i, s in enumerate(lines):
        if not s:
            if paras[-1]:
                paras.append([])
            continue
        cur = paras[-1]
        nxt = next((x for x in lines[i + 1:] if x), "")
        if len(s) < 0.6 * full and not s.endswith(END) and nxt[:1].isupper() and not re.search(r"\w-$", s):
            if cur and not cur[-1].endswith(END):
                cur[-1] = f"{cur[-1]} {s}."
            else:
                cur.append(s + ".")
            paras.append([])
        elif re.match(r"^([-•▪●*]|\d+[.)])\s", s) and cur:
            paras.append([s])
        elif not cur or cur[-1].endswith((".", "!", "?", ":", "…")):
            cur.append(s)
        elif re.search(r"\w-$", cur[-1]):
            cur[-1] = cur[-1][:-1] + s
        else:
            cur[-1] = f"{cur[-1]} {s}"
    return "\n\n".join("\n".join(p) for p in paras if p)


# ------------------------------------------------------------------ public

def parse(path: str | Path, out_dir: str | Path, *, filename: str | None = None) -> dict[str, Any]:
    """Text with `[ảnh N]` markers where image N appears, and images[N-1] = {path, width, height}."""
    path = Path(path)
    ext = Path(filename or path.name).suffix.lower()
    if ext not in SUPPORTED:
        raise DocumentError(f"Định dạng {ext or '?'} chưa hỗ trợ — dùng .docx, .pdf (hoặc .doc/.rtf/.odt).")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    c = _Collector(out_dir)
    if ext in CONVERT:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"in{ext}"
            shutil.copy(path, src)
            proc = subprocess.run(["/usr/bin/textutil", "-convert", "docx", "-output", str(Path(tmp) / "out.docx"),
                                   str(src)], capture_output=True, text=True, timeout=120)
            if proc.returncode != 0 or not (Path(tmp) / "out.docx").exists():
                raise DocumentError(f"Không chuyển được {ext} sang .docx: {proc.stderr.strip()[:200]}")
            title = _docx(Path(tmp) / "out.docx", c)
        kind = "word"
    elif ext == ".docx":
        try:
            title = _docx(path, c)
        except DocumentError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DocumentError(f"Không đọc được file Word: {e}") from e
        kind = "word"
    else:
        title = _pdf(path, c)
        kind = "pdf"
    first = next((b for b in c.blocks if not MARKER_RE.fullmatch(b) and len(b) <= 120), "")
    res = c.result(title or first.rstrip(".") or Path(filename or path.name).stem, kind)
    if not res["text"].strip() or not MARKER_RE.sub("", res["text"]).strip():
        raise DocumentError("Không tìm thấy chữ trong tài liệu.")
    return res


def import_file(path: str | Path, data_dir: Path, *, filename: str | None = None) -> dict[str, Any]:
    """parse() into data/uploads/docs/<id>/ and return it with the id."""
    doc_id = uuid.uuid4().hex[:10]
    out = data_dir / "uploads" / "docs" / doc_id
    try:
        res = parse(path, out, filename=filename)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return {"id": doc_id, **res}


# ------------------------------------------------------------------ markers in scripts

def strip_markers(text: str) -> str:
    """Text to speak: markers removed, the blank lines they leave collapsed."""
    text = re.sub(r"[ \t]*" + MARKER_RE.pattern + r"[ \t]*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^ | $", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_markers(text: str) -> list[tuple[int | None, str]]:
    """[(image number or None, text after it until the next marker)]; the first item has None."""
    parts: list[tuple[int | None, str]] = []
    last, cur = 0, None
    for m in MARKER_RE.finditer(text):
        parts.append((cur, text[last:m.start()]))
        cur, last = int(m.group(1)), m.end()
    parts.append((cur, text[last:]))
    return [(n, t.strip()) for n, t in parts if n is not None or t.strip()]


def has_markers(text: str | None) -> bool:
    return bool(text and MARKER_RE.search(text))
