# SDD — PDF import: one word per line (shredded text layer)

Status: v1.0 (implemented) · 2026-10-15 · Owner: tu.dev@block8.ai · Fixes
[SDD-doc-import-preview-chibi.md](SDD-doc-import-preview-chibi.md) §3.1 (PDF branch of `text/documents.py`)

## 1. Problem

Importing a PDF exported from **Google Docs** (`/Producer: Skia/PDF m157 Google Docs Renderer`) fills the
Nội dung box with **one word per line, each word its own paragraph**. The text can't be read or edited, and it
can't be narrated: every word becomes a scene/sentence, with a full stop or pause after it.

Evidence (`P4 5 video.pdf`, 10 pages, analysed on a copy, CPU only):

| Page | chars | lines from `page.extract_text()` | blank lines | non-blank lines holding a single token |
|---|---|---|---|---|
| 1 | 2 106 | 629 | 315 | 100 % |
| 2 | 2 603 | 773 | 387 | 100 % |
| 3–10 | — | 396–1 269 | ≈ half | 100 % on every page |

Raw output: `'Phần\n \n4.\n \nTrách\n \nnhiệm\n \nxã\n \nhội…'`.

### 1.1 Root cause

1. **The producer.** Skia draws every word with its own text-show operator and positions it absolutely. Each
   inter-word space is a separate operator too. pypdf's default (`plain`) extraction ends a "line" at every
   operator that moves the text matrix, so the result alternates `word`, `\n`, `" "`, `\n`.
2. **Our post-processing makes it worse.** `_unwrap()` strips each line, so the `" "` lines become empty, and
   an empty line means a **paragraph break**. `_pdf()` then splits on `\n\s*\n` and `_Collector.text()` appends
   each word as its own block. The blocks are joined with `\n\n`, which gives one word per paragraph.

Word-exported PDFs (the case tested in v1.0) emit whole lines per operator, so they were not affected.

### 1.2 Secondary defect: numbered headings get glued together

`_unwrap()` treats a short line as a heading only when the **next line starts with an upper-case letter**
(`nxt[:1].isupper()`). A numbered heading followed by another numbered heading (`Phần 4. …` → `4.1. …`) is not
detected, so the two get glued together: `"Phần 4. Trách nhiệm … bền vững 4.1. Hồ sơ CSR …"`. Vietnamese reports
number almost every heading, so this hits Word-exported PDFs as well.

### 1.3 Why pypdf's layout mode alone is not enough (prototype)

`extract_text(extraction_mode="layout")` rebuilds the **visual lines** correctly (page 1 drops from 629 lines
to 20). It gives **no paragraph boundaries**, though:

- With `layout_mode_space_vertically=False` there are no blank lines at all.
- With `True`, blank lines appear only around figures (2 on page 1, 0 on page 3).
- Text is justified, so line length doesn't help either. Collapsed lengths within one paragraph range from 76
  to 94 characters, and the last line of one paragraph (`đặt chức năng … trực tiếp cho …`) is 81 % of the widest.
  It can't be told apart from a mid-paragraph line.

Layout mode followed by `_unwrap` would therefore merge consecutive body paragraphs into one block.

## 2. Requirements

| # | Requirement |
|---|---|
| R1 | A Google Docs / Skia PDF, or any producer that emits one word per operator, imports as **one block per paragraph**: not per word, not per visual line, and consecutive paragraphs not merged. |
| R2 | PDFs that import correctly today (Word/LibreOffice exports) give the **same result** as v1.0, apart from the R3 heading fix. |
| R3 | Headings, including numbered ones (`Phần 4.`, `Chương 2`, `4.1.`, `4.1.2`), are separate blocks ending in a full stop, so the narration pauses after them. |
| R4 | No new dependency unless pypdf cannot do the job. Licence rule: Apache/MIT/BSD only (SDD §3). PyMuPDF stays rejected (AGPL). |
| R5 | The render in progress must not be disturbed: no restart, no venv sync and no edits in the live checkout while a job runs (§6). |
| R6 | `[ảnh N]` markers, image extraction, the scanned-PDF rejection and the 60 000-character cap behave as before. |

## 3. Design

### 3.1 Per-page detection (R1, R2)

Decide **per page**, since a document can mix producers (for example a Google Docs body with a cover page from
Word):

```
page ─► plain = page.extract_text()
        _shredded(plain)? ──no──► _unwrap(plain)  (v1.0 path, + §3.3 heading fix) ──┐
              │yes                                                                  ├─► paragraphs ─► c.text()
              └─► _geometric(page)  (§3.2) ────────────────────────────────────────┘
                  raises / returns nothing → layout mode + _unwrap → else plain, with a log warning
```

**`_shredded(raw) -> bool`.** Take the non-blank lines. If there are at least **3** of them and **≥ 60 %** hold a
single token, the page is shredded. Normal prose has 8–15 words per line, far below the threshold, and the
sample is at 100 %. There is deliberately no high line minimum: a shredded title or cover page has only a few
words, and that is exactly where it would otherwise stay one word per paragraph. A false positive, such as a
table of contents with single-word lines, only sends the page down the geometric path, which reads it
correctly anyway.

The rule depends on what the text looks like, not on `/Producer`. Canva, some browsers' "Save as PDF" and some
LaTeX fonts shred in the same way. `/Producer` is logged at debug level only.

### 3.2 Geometric reconstruction for shredded pages (R1, R3)

pypdf's `extract_text(visitor_text=cb)` calls `cb(text, cm, tm, font_dict, font_size)` for every fragment.
From those calls we build lines, then paragraphs, from the **real page geometry**. This needs no new
dependency. Measured on the sample:

| Signal | Page 1 | Page 3 |
|---|---|---|
| Line pitch inside a paragraph | 17.2–17.3 pt | 17.2–17.3 pt |
| Gap between body paragraphs | 23.2 pt (1.35 ×) | 23.2 pt |
| Gap before / after a heading | 26.4–29.2 / 19.0 pt | — |
| Font size: body / H2 / H1 | 12 / 13 / 15 | 12 |

Algorithm:

1. **Fragments.** Skip whitespace-only text. Position = `tm[4:6]` mapped through `cm`. Effective size =
   `font_size × |scale|`. The **body size** is the size carrying the most characters (not the most lines, which
   ties on a two-line cover page).
2. **Lines.** Fragments stay in **content-stream order** (not sorted by x): Skia also splits single words over
   operators (`T` + `rách`), and the space operators sit between words. A fragment joins the current line when
   `|Δy| < 0.5 × size`; whitespace fragments become a space.
   This tolerates baseline jitter and superscripts such as footnote marks. Fragments are joined with one space
   only where a space was drawn; a space before closing punctuation (`, . ; : ) ” …`) is dropped. A line ending in
   a hyphen inside a word is joined to the next without the hyphen.
3. **Paragraphs.** `pitch` = the median gap between consecutive lines on the page, and `body` = the most common
   size. A new paragraph starts when:
   - the gap is greater than **1.2 × pitch** (the sample shows 1.35 × for a paragraph break, and 1.00 × inside one);
   - the line's size differs from the previous line's size by more than 0.5 pt (a heading, or the line after it);
   - the line starts with a bullet or a numbered-list token (the existing `_unwrap` rule).
4. **Headings.** A line whose size is greater than `body` + 0.5 pt is a heading, whatever its length: it is its
   own block and is closed with a full stop. The §3.3 pattern catches numbered headings set in body size.
5. **Fallback.** If fewer than 2 lines come out, or the callback raises, use layout mode + `_unwrap`. If that
   also fails, fall back to plain text (a warning is logged).

On the sample, page 1 gives `Phần 4. …` (15 pt) | `4.1. …` (13 pt) | the body paragraph | `6.1. Phong cách
lãnh đạo` | `4.2. …` (13 pt) | the next body paragraph. Page 3 splits at `…với thực tế.` / `Phép thử thứ ba…` and at
`…trực tiếp cho …` / `Góc nhìn của nhóm…`. These are exactly the boundaries that layout mode lost (§1.3).

Single-column reading order is assumed, as in v1.0. A line that goes back **up** the page (a text box or a figure label) always starts a new block. A ragged last line (ends in `. ! ? …` and is < 0.75 × the longest line) also ends its paragraph, for producers without paragraph spacing.

### 3.3 `_unwrap()` heading fix (R3, applies to the plain path too)

```
HEADING_RE = r"^(?:(?:Phần|Chương|Mục|Bài|Part|Chapter|Section)\s+\w+|\d+(?:\.\d+)+\.?|\d+[.)])\s+\S"
```

The pattern requires a keyword, a dotted number (`4.1`, `4.1.2`) or `N.` / `N)`. A sentence that starts with a
bare number, such as `2025 là năm…`, is not taken for a heading.

1. A line that matches `HEADING_RE`, is short (< 0.6 × the longest line) and has no end punctuation always
   **starts a new paragraph and is closed** with a full stop. This applies even when the previous line has no end
   punctuation, which is the `Phần 4. …` → `4.1. …` case.
2. The "next line starts a new sentence" test accepts a digit as well as an upper-case letter.

A long numbered line (≥ 0.6 × the longest line) is a list item, not a heading. It keeps the current rule: a new
paragraph, with no forced full stop.

### 3.4 Unchanged

`_Collector`, the image path (`page.images`, MIN_SIDE, SHA-1 dedupe, MAX_IMAGES), the scanned-PDF check
(`n_chars < 20`, counted on the chosen text), MAX_CHARS, `POST /api/documents`, the dashboard, MCP and the
`[ảnh N]` contract with `pipelines`. Only `_pdf()` and `_unwrap()` change, plus the helpers `_shredded()` and
`_geometric()`.

### 3.5 Known limitations (out of scope)

- **Marker position inside a page.** `[ảnh N]` is still placed after all of the page's text. The geometric
  path knows the `y` of each line, and the image's placement `cm` would give its `y` too, so a follow-up could
  insert the marker where the figure really is.
- **Text drawn inside figures.** Diagram labels that are real vector text are read as body text, in every
  extraction mode. For example `6.1. Phong cách lãnh đạo` on page 1 of the sample appears in the plain output
  too. The owner can delete them in the text box.
- **Multi-column PDFs, running headers and page numbers** are unchanged from v1.0.
- **Scanned PDFs / OCR** are still rejected (v1.0 §3.1).

### 3.6 Alternatives considered

| Option | Verdict |
|---|---|
| Geometric lines/paragraphs from `visitor_text`, shredded pages only | **Chosen.** No new dependency. Its signals (pitch, gap, size) were measured on the sample. |
| pypdf layout mode + `_unwrap` | Lines are right, paragraphs are lost (§1.3). Kept as the first fallback. |
| Layout mode for every page | Changes clean PDFs (justified-gap spacing, floating text moved), which breaks R2. |
| pdfminer.six / pdfplumber (MIT) | Good paragraph analysis, but a new dependency and slower. Revisit for multi-column layouts or figure text. |
| PyMuPDF | Rejected: AGPL-3.0 (SDD §3). |
| Joining single-word lines inside `_unwrap` | The `" "` lines carry no gap information, so real paragraph breaks are lost. |

## 4. Tests

Result: 83 passed (whole suite). On a copy of the owner's PDF: 80 blocks (was 3 000+), 0 single-word
blocks, 8 images, headings `Phần 4.` / `4.1.` separate, page-3 boundaries as listed in §3.2.


In `tests/test_documents.py` (added to the existing file). CPU only, no model, no GPU:

| Test | Checks |
|---|---|
| `test_pdf_shredded_words` | Fixture PDF generated in the test in Skia's exact pattern (flipped page, each word and space in its own `BT … Tm … Td … Tj ET`; a plain `Tm`-per-word PDF is *not* shredded by pypdf), two paragraphs 1.35 × pitch apart, a 15 pt heading. Use a hand-written content stream (no new dependency), or reportlab (BSD) as a test-only dependency. Expect exactly heading + 2 paragraphs, and no single-word block. |
| `test_pdf_shredded_short_page` | A 5-word shredded title page gives one block. |
| `test_pdf_clean_unchanged` | A clean one-line-per-operator PDF gives the same text as v1.0 (golden string, recorded on main before the change). |
| `test_shredded_threshold` | `_shredded` is false for prose, true at ≥ 60 % single-token lines, true for 3 lines. |
| `test_unwrap_numbered_headings` | `"Phần 4. Trách nhiệm\n4.1. Hồ sơ CSR\nPNJ là …"` gives three blocks, each heading ending in `.`; `"2025 là năm…"` is not a heading. |
| `test_unwrap_existing` | Hyphen join, bullet start and a long numbered list line are unchanged. |
| `test_geometric_failure_falls_back` | When the visitor raises (monkeypatched), layout mode is used, then plain text, and a warning is logged. |

The owner's PDF is **not** committed: it stays in `data/` (git-ignored). Before merge, run a manual check on a
copy of it: count the blocks and spot-check pages 1 and 3 against the boundaries listed in §3.2.

## 5. Files

| File | Change |
|---|---|
| `src/mediaforge/text/documents.py` | `_shredded()`, `_geometric()`, per-page choice in `_pdf()`, `HEADING_RE` + digit rule in `_unwrap()` |
| `tests/test_documents.py` | the cases in §4 |
| `docs/SDD-doc-import-preview-chibi.md` | §3.1: a pointer to this SDD (done) |
| `docs/SDD.md` | the `text/documents.py` line points to this SDD (done) |

## 6. Delivery without disturbing the running render (R5)

- **The service does not run from this checkout.** launchd runs a non-editable copy installed with
  `uv tool install` (`~/.local/bin/mediaforge`, data in `~/.local/share/mediaforge`), because TCC blocks
  background agents from reading `~/Documents`. Editing `src/` or syncing the project `.venv` therefore never
  touches the live process. (The `uv run` re-sync seen during the analysis only reinstalled the project
  `.venv`, never the service.) A git worktree on a branch is still used, so `main` only ever holds tested code.
- **Tests** run in the worktree's own venv (`uv run --with pytest pytest`). They are CPU only and need no model
  (memory: never GPU-test beside a live render).
- **Deploy** = merge to main, then **`scripts/service.sh update`**. It reinstalls the tool copy and **waits
  until `/api/health` shows no running job** (up to 30 min) before `launchctl kickstart`. A plain `restart` only
  restarts the old installed copy and does **not** deploy code. Check `queue` and `running` before you start
  too: `update` waits for the running job, but a queued job would start in the gap.
  Done 2026-10-15: `queue: 0, running: null`, then `update`, then re-importing the sample through
  `POST /api/documents` gave 80 blocks, 0 single-word blocks and 8 images.
- **Rollback:** revert the merge commit and run `service.sh update`. There is no data migration.
  Documents imported earlier keep their (bad) text. Import them again after the deploy.
