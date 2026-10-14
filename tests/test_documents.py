"""Word/PDF import: text order, image markers, filtering (CPU only, no models)."""

import io

import pytest
from PIL import Image

from mediaforge.text import documents as D


def _png(w: int, h: int, color=(200, 40, 40)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def docx_file(tmp_path):
    import docx

    d = docx.Document()
    d.add_heading("Bảo mật mật khẩu", level=1)
    d.add_paragraph("Mật khẩu mạnh cần dài.")
    d.add_picture(io.BytesIO(_png(400, 300)))
    d.add_paragraph("Hãy dùng trình quản lý mật khẩu.")
    d.add_picture(io.BytesIO(_png(40, 40, (0, 0, 255))))  # icon: dropped
    d.add_picture(io.BytesIO(_png(400, 300)))  # duplicate of image 1: dropped
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "MFA"
    t.rows[0].cells[1].text = "Bật cho mọi tài khoản"
    d.add_picture(io.BytesIO(_png(500, 500, (20, 160, 20))))
    d.add_paragraph("Kết thúc.")
    p = tmp_path / "in.docx"
    d.save(p)
    return p


def test_docx_text_images_and_markers_in_order(docx_file, tmp_path):
    r = D.parse(docx_file, tmp_path / "out")
    assert r["kind"] == "word" and r["title"] == "Bảo mật mật khẩu"
    t = r["text"]
    assert len(r["images"]) == 2
    order = ["Mật khẩu mạnh", "[ảnh 1]", "trình quản lý", "MFA — Bật cho mọi tài khoản.", "[ảnh 2]", "Kết thúc."]
    pos = [t.index(s) for s in order]
    assert pos == sorted(pos)
    assert all(Image.open(im["path"]).format == "PNG" for im in r["images"])
    assert any("nhỏ" in w for w in r["warnings"])


def test_pdf_text_then_page_images(tmp_path):
    from pypdf import PdfReader, PdfWriter

    # A text page (hand-written minimal PDF) followed by an image page that Pillow writes.
    text_pdf = tmp_path / "t.pdf"
    stream = b"BT /F1 18 Tf 72 720 Td (Hello security team. This is page one.) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>stream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    text_pdf.write_bytes(out)
    img_pdf = tmp_path / "i.pdf"
    Image.new("RGB", (600, 400), (10, 120, 200)).save(img_pdf, "PDF")
    w = PdfWriter()
    for f in (text_pdf, img_pdf):
        for pg in PdfReader(f).pages:
            w.add_page(pg)
    doc = tmp_path / "doc.pdf"
    w.write(doc)

    r = D.parse(doc, tmp_path / "out")
    assert r["kind"] == "pdf" and len(r["images"]) == 1
    assert r["text"].index("Hello security team") < r["text"].index("[ảnh 1]")


def test_scanned_pdf_is_rejected(tmp_path):
    p = tmp_path / "scan.pdf"
    Image.new("RGB", (600, 800), "white").save(p, "PDF")
    with pytest.raises(D.DocumentError, match="scan"):
        D.parse(p, tmp_path / "out")


def test_unsupported_format(tmp_path):
    p = tmp_path / "x.pptx"
    p.write_bytes(b"x")
    with pytest.raises(D.DocumentError):
        D.parse(p, tmp_path / "out")


def test_unwrap_joins_pdf_lines():
    raw = "Mật khẩu mạnh cần\ndài và khó đoán.\nCâu thứ hai.\n\n- gạch đầu dòng\n- thứ hai\nsecu-\nrity"
    assert D._unwrap(raw) == ("Mật khẩu mạnh cần dài và khó đoán.\nCâu thứ hai.\n\n- gạch đầu dòng\n\n"
                              "- thứ hai security")


def test_markers_split_and_strip():
    text = "Mở đầu.\n\n[ảnh 1]\n\nThân bài.\n\n[Image 2]\n\nKết."
    assert D.split_markers(text) == [(None, "Mở đầu."), (1, "Thân bài."), (2, "Kết.")]
    assert D.strip_markers(text) == "Mở đầu.\n\nThân bài.\n\nKết."
    assert D.split_markers("[ảnh 3] Chỉ có ảnh.") == [(3, "Chỉ có ảnh.")]
    assert not D.has_markers("không có ảnh")


def test_unwrap_separates_pdf_headings():
    raw = ("Tiêu đề ngắn\nMột đoạn văn rất dài kéo dài qua nhiều dòng của trang PDF và vẫn chưa hết câu\n"
           "tiếp tục ở đây.")
    assert D._unwrap(raw) == ("Tiêu đề ngắn.\n\nMột đoạn văn rất dài kéo dài qua nhiều dòng của trang PDF và vẫn "
                              "chưa hết câu tiếp tục ở đây.")
