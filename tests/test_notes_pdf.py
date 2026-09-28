import re
import shutil
import subprocess
import zlib

import pytest

gi = pytest.importorskip("gi")
from launcher.notes.pdf import export_pdf  # noqa: E402

needs_poppler = pytest.mark.skipif(
    not (shutil.which("pdftotext") and shutil.which("pdfinfo")), reason="needs poppler-utils"
)

NOTE = (
    "# Lecture 3: Fourier Series\n\n"
    "Periodic signals as **sums** of *sinusoids*.\n\n"
    "## Key ideas\n- Orthogonality\n\t- sin and cos\n1. Find the period\n10. Compute aₙ\n"
    "- [x] Read chapter 3\n- [ ] Problem set 2\n\n"
    "> Any periodic function\n\n"
    "See [the lecture page](https://example.edu/signals), https://gnome.org and "
    "[my other note](Other.md).\n\n"
    "---\n```python\nx = **not bold**\n```\n"
    "### Détails 中文\n![](attachments/pic.png)\n![](attachments/missing.png)\n"
)


def pdf_text(path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                          text=True, check=True).stdout  # fmt: skip


def pdf_info(path, *args) -> str:
    return subprocess.run(["pdfinfo", *args, str(path)], capture_output=True, text=True,
                          check=True).stdout  # fmt: skip


def inflated(path) -> bytes:
    """The PDF with its compressed streams (where cairo keeps the bookmarks) inflated."""
    data = path.read_bytes()
    out = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            out.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return b"\n".join(out)


def picture(path, width=400, height=200):
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x3584E4FF)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixbuf.savev(str(path), "png", [], [])


@pytest.fixture
def exported(tmp_path):
    picture(tmp_path / "attachments" / "pic.png")
    out = tmp_path / "out.pdf"
    pages = export_pdf(NOTE, out, title="Lecture 3", image_path=lambda url: tmp_path / url)
    return out, pages


@needs_poppler
def test_text_reads_like_the_note(exported):
    out, pages = exported
    assert pages == 1
    text = pdf_text(out)
    for shown in (
        "Lecture 3: Fourier Series",
        "Periodic signals as sums of sinusoids.",
        "Orthogonality",
        "sin and cos",
        "1. Find the period",
        "10. Compute aₙ",
        "Read chapter 3",
        "Problem set 2",
        "Any periodic function",
        "See the lecture page, https://gnome.org and my other note.",
        "x = **not bold**",  # code is printed as it is
        "Détails 中文",
        "![](attachments/missing.png)",  # a picture that can't be loaded: its link
    ):
        assert shown in text, shown
    for markup in ("# Lecture", "**sums**", "- [x]", "> Any", "```", "](https", "---"):
        assert markup not in text, markup


@needs_poppler
def test_metadata_and_web_links(exported):
    out, _ = exported
    info = pdf_info(out)
    assert re.search(r"Title:\s+Lecture 3", info)
    assert re.search(r"Page size:\s+595.* x 841.*A4", info)
    urls = pdf_info(out, "-url")
    assert "https://example.edu/signals" in urls and "https://gnome.org" in urls
    assert "Other.md" not in urls  # links to notes only open inside Notes


def test_headings_are_bookmarks(exported):
    out, _ = exported
    data = inflated(out)
    assert b"/Outlines" in data
    titles = re.findall(rb"/Title \(([^)]*)\)", data)
    assert b"Lecture 3: Fourier Series" in titles and b"Key ideas" in titles


@needs_poppler
def test_long_notes_break_across_pages(tmp_path):
    words = " ".join(f"word{i}" for i in range(4000))
    lines = "\n".join(f"- item {i}" for i in range(80))
    out = tmp_path / "long.pdf"
    pages = export_pdf(f"# Long\n{words}\n## List\n{lines}\n", out, title="Long")
    assert pages >= 4
    assert re.search(rf"Pages:\s+{pages}\b", pdf_info(out))
    text = pdf_text(out)
    assert "word0 " in text and "word3999" in text  # the paragraph is split, not cut
    assert all(f"item {i}" in text for i in (0, 40, 79))


def test_big_pictures_are_scaled_down(tmp_path):
    picture(tmp_path / "big.png", 3000, 4000)
    out = tmp_path / "pic.pdf"
    assert export_pdf("![](big.png)\n", out, title="Pic", image_path=lambda u: tmp_path / u) == 1


def test_empty_note_and_unwritable_path(tmp_path):
    assert export_pdf("", tmp_path / "empty.pdf", title="Untitled") == 1
    with pytest.raises(OSError):  # cairo.IOError, which the window reports
        export_pdf("x", tmp_path / "missing" / "x.pdf", title="x")


def test_failed_export_keeps_the_old_file(tmp_path, monkeypatch):
    out = tmp_path / "keep.pdf"
    out.write_bytes(b"old")

    def broken(self, text):
        raise OSError("disk full")

    monkeypatch.setattr("launcher.notes.pdf._Writer.write", broken)
    with pytest.raises(OSError):
        export_pdf("x", out, title="x")
    assert out.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["keep.pdf"]  # no leftovers
