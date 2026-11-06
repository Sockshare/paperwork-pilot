"""Turn an uploaded file into plain text.

Supported:
    .pdf            pypdf, with `pdftotext` (poppler) as a fallback for stubborn files
    .docx           python-docx
    .txt .md .csv   read as text
    .png .jpg .jpeg .webp .tif .tiff .bmp
                    OCR with Tesseract if it is installed (optional)

Every function returns an ExtractResult. It never raises for a bad file; it
returns a friendly `error` string instead so the UI can show it.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 120_000  # ~30k words; plenty for any household document
MAX_DOCX_UNCOMPRESSED = 60 * 1024 * 1024  # guards against "zip bomb" .docx files
MAX_IMAGE_PIXELS = 30_000_000  # ~30 megapixels; bigger photos are shrunk before OCR

TEXT_EXTS = {".txt", ".md", ".csv", ".log", ".text"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
SUPPORTED_EXTS = {".pdf", ".docx"} | TEXT_EXTS | IMAGE_EXTS


@dataclass
class ExtractResult:
    text: str = ""
    pages: int = 0
    method: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())


def extract_text(filename: str, data: bytes) -> ExtractResult:
    ext = Path(filename or "").suffix.lower()
    if not data:
        return ExtractResult(error="The file is empty.")
    if ext not in SUPPORTED_EXTS:
        return ExtractResult(
            error=(
                f"Sorry, '{ext or 'this'}' files are not supported yet. "
                "Try a PDF, Word (.docx), text file, or a photo (.jpg/.png)."
            )
        )
    try:
        if ext == ".pdf":
            res = _from_pdf(data)
        elif ext == ".docx":
            res = _from_docx(data)
        elif ext in TEXT_EXTS:
            res = _from_text(data)
        else:
            res = _from_image(data)
    except Exception as exc:  # noqa: BLE001 - we want a friendly message no matter what
        return ExtractResult(error=f"The file could not be read ({type(exc).__name__}). Try saving it again or as a PDF.")

    if res.ok and len(res.text) > MAX_CHARS:
        res.text = res.text[:MAX_CHARS]
        res.warnings.append("This document is very long, so only the first part was analyzed.")
    return res


# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def _from_text(data: bytes) -> ExtractResult:
    # UTF-16 only when a byte-order mark says so; otherwise an ordinary Latin-1 file
    # would "decode" into CJK garbage.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "cp1252", "latin-1")
    for enc in encodings:
        try:
            return ExtractResult(text=_clean(data.decode(enc)), pages=1, method="text")
        except UnicodeDecodeError:
            continue
    return ExtractResult(error="The text file uses an encoding the app could not read.")


def _from_docx(data: bytes) -> ExtractResult:
    import zipfile

    import docx  # python-docx

    # A .docx is a zip. Refuse ones that would expand to something enormous.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile:
        return ExtractResult(error="This doesn't look like a valid Word file. Try saving it again as .docx.")
    if total > MAX_DOCX_UNCOMPRESSED:
        return ExtractResult(error="This Word file is too large to open safely. Copy the text and paste it instead.")

    document = docx.Document(io.BytesIO(data))
    parts: list[str] = []
    size = 0
    for para in document.paragraphs:
        parts.append(para.text)
        size += len(para.text)
        if size > MAX_CHARS:
            break
    if size <= MAX_CHARS:
        for table in document.tables:
            for row in table.rows:
                line = " | ".join(c.text.strip() for c in row.cells)
                parts.append(line)
                size += len(line)
                if size > MAX_CHARS:
                    break
            if size > MAX_CHARS:
                break
    text = _clean("\n".join(parts))
    if not text:
        return ExtractResult(error="The Word file has no readable text.")
    return ExtractResult(text=text, pages=1, method="docx")


def _from_pdf(data: bytes) -> ExtractResult:
    text = ""
    pages = 0
    if not data.lstrip().startswith(b"%PDF"):
        return ExtractResult(error="This file doesn't look like a real PDF (it may be damaged or renamed). Try opening it in a PDF viewer and saving it again.")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001
                unlocked = 0
            if not unlocked:  # PasswordType.NOT_DECRYPTED == 0
                return ExtractResult(error="This PDF is password protected. Remove the password and try again.")
        pages = len(reader.pages)
        chunks = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                chunks.append("")
        text = _clean("\n\n".join(chunks))
    except Exception:  # noqa: BLE001
        text = ""

    # Fallback: poppler's pdftotext handles many PDFs pypdf struggles with.
    if len(text) < 40 and shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            try:
                out = subprocess.run(
                    ["pdftotext", "-layout", tmp.name, "-"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                alt = _clean(out.stdout)
                if len(alt) > len(text):
                    text = alt
            except (subprocess.SubprocessError, OSError):
                pass

    if len(text) < 40:
        # Probably a scanned PDF (just pictures). Try OCR if we can.
        ocr = _ocr_pdf(data)
        if ocr.ok:
            ocr.warnings.insert(0, "This PDF looked like a scan, so the text was read with OCR. Check numbers carefully.")
            return ocr
        return ExtractResult(
            error=(
                "This PDF seems to be a scanned picture with no text layer. "
                + ("Install Tesseract OCR to read scans (see Troubleshooting), " if not _has_tesseract() else "OCR could not read it, ")
                + "or paste the text in by hand."
            )
        )
    return ExtractResult(text=text, pages=pages, method="pdf")


# ---------------------------------------------------------------------------
def _has_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_image_bytes(data: bytes) -> str:
    """OCR a single image using the tesseract CLI (no extra Python package needed)."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS * 4  # hard stop for absurd files
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("L")
    pixels = img.width * img.height
    if pixels > MAX_IMAGE_PIXELS:
        # Shrink huge photos; OCR doesn't need more than ~30 MP.
        scale = (MAX_IMAGE_PIXELS / pixels) ** 0.5
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    elif img.width < 1500 and img.height < 20_000:
        # Upscale small photos: tesseract likes ~300 dpi text. Cap the result.
        scale = min(1500 / img.width, (MAX_IMAGE_PIXELS / pixels) ** 0.5)
        if scale > 1:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        img.save(tmp.name, format="PNG")
        out = subprocess.run(
            ["tesseract", tmp.name, "-", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return out.stdout


def _from_image(data: bytes) -> ExtractResult:
    if not _has_tesseract():
        return ExtractResult(
            error=(
                "Reading photos needs the free Tesseract OCR tool, which is not installed. "
                "On Ubuntu/Debian run:  sudo apt install tesseract-ocr   then try again. "
                "Or paste the text in by hand."
            )
        )
    text = _clean(_ocr_image_bytes(data))
    if len(text) < 20:
        return ExtractResult(error="The photo was too blurry or dark to read. Try a clearer, straight-on photo in good light.")
    return ExtractResult(
        text=text,
        pages=1,
        method="ocr",
        warnings=["Text was read from a photo with OCR. Double-check names, dates and dollar amounts."],
    )


def _ocr_pdf(data: bytes) -> ExtractResult:
    if not _has_tesseract() or not shutil.which("pdftoppm"):
        return ExtractResult(error="no ocr")
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "in.pdf"
        pdf_path.write_bytes(data)
        try:
            subprocess.run(
                ["pdftoppm", "-r", "200", "-png", "-l", "10", str(pdf_path), str(Path(tmpdir) / "pg")],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            return ExtractResult(error="no ocr")
        pages = sorted(Path(tmpdir).glob("pg*.png"))
        texts = []
        for p in pages:
            try:
                texts.append(_ocr_image_bytes(p.read_bytes()))
            except Exception:  # noqa: BLE001
                texts.append("")
        text = _clean("\n\n".join(texts))
        if len(text) < 40:
            return ExtractResult(error="no ocr")
        warnings = []
        if len(pages) >= 10:
            warnings.append("Only the first 10 pages of the scan were read.")
        return ExtractResult(text=text, pages=len(pages), method="ocr", warnings=warnings)
