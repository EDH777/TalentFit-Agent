from collections import Counter
from functools import lru_cache
from io import BytesIO
from math import sin
import re
from threading import Lock
import unicodedata

import numpy as np
from docx import Document
from PIL import Image
from pypdf import PdfReader

from app.core.config import settings

# PDFium disallows simultaneous native calls, including on different documents.
_PDF_LOCK = Lock()


def _corrupt_text(value: str) -> bool:
    markers = ''.join('?' if char == '\ufffd' or unicodedata.category(char) in {'Cn', 'Co', 'Cs', 'Cc'} else '.'
                      for char in value if not char.isspace())
    bad = markers.count('?')
    # Isolated Wingdings bullets/icons are not a broken text layer.
    return bad >= 3 and bad > len(markers) * 0.01 and ('???' in markers or bad > len(markers) * 0.2)


def _positioned_text(textpage, rotation: int = 0) -> tuple[str, bool, set[str]]:
    from pypdfium2 import raw

    codes = [raw.FPDFText_GetUnicode(textpage, index) for index in range(textpage.count_chars())]
    sequence = ''.join(chr(code) if code <= 0x10FFFF else '\ufffd' for code in codes)
    matches = list(re.finditer(r'[A-Za-z0-9_+/=-]{32,}', sequence))
    counts = Counter(match[0] for match in matches)
    # Repeated angled tracking IDs also occur in a horizontal footer. Remove all
    # copies only with rotation evidence; normal long IDs / code remain intact.
    watermarks = {match[0] for match in matches if counts[match[0]] >= 3 and any(
        abs(sin(raw.FPDFText_GetCharAngle(textpage, index) * 2)) > 0.1
        for index in range(match.start(), match.end())
    )}
    excluded = {index for match in matches if match[0] in watermarks for index in range(match.start(), match.end())}
    corrupt = _corrupt_text(''.join(char for index, char in enumerate(sequence) if index not in excluded))
    glyphs = []
    for index, char in enumerate(sequence):
        if index in excluded or char.isspace() or char == '\x00':
            continue
        left, bottom, right, top = textpage.get_charbox(index, loose=True)
        # PDFium charboxes are in unrotated PDF coordinates; sort in displayed axes.
        if rotation == 90:
            left, bottom, right, top = bottom, -right, top, -left
        elif rotation == 180:
            left, bottom, right, top = -right, -top, -left, -bottom
        elif rotation == 270:
            left, bottom, right, top = -top, left, -bottom, right
        if top > bottom:
            glyphs.append((left, bottom, right, top, char))
    glyphs.sort(key=lambda glyph: (-(glyph[1] + glyph[3]) / 2, glyph[0]))
    lines = []
    for glyph in glyphs:
        center, height = (glyph[1] + glyph[3]) / 2, glyph[3] - glyph[1]
        nearby = [line for line in lines[-3:] if abs(line['y'] - center) <= max(2, min(line['height'], height) * 0.4)]
        if nearby:
            line = min(nearby, key=lambda item: abs(item['y'] - center))
        else:
            line = {'y': center, 'height': height, 'glyphs': []}
            lines.append(line)
        line['glyphs'].append(glyph)
    result = []
    for line in sorted(lines, key=lambda item: -item['y']):
        value, previous_right = '', None
        for left, bottom, right, top, char in sorted(line['glyphs'], key=lambda glyph: glyph[0]):
            gap = left - previous_right if previous_right is not None else 0
            adjacent_chinese = bool(value and '\u4e00' <= value[-1] <= '\u9fff' and '\u4e00' <= char <= '\u9fff')
            if gap > max(1, line['height'] * 0.13) and (not adjacent_chinese or gap > line['height']):
                value += ' '
            value += char
            previous_right = right
        result.append(value)
    return '\n'.join(result).strip(), corrupt, watermarks


@lru_cache(maxsize=1)
def ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def image_text(image: Image.Image) -> str:
    image = image.convert("RGB")
    image.thumbnail((2400, 3400))
    result, _ = ocr_engine()(np.array(image))
    return "\n".join(item[1] for item in (result or []) if item[2] >= 0.5)


def extract_text(filename: str, data: bytes) -> str:
    extension = filename.lower().rsplit(".", 1)[-1]
    if extension in {"jpg", "jpeg", "png"}:
        with Image.open(BytesIO(data)) as image:
            return image_text(image)
    if extension == "docx":
        doc = Document(BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        lines.extend(" | ".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows)
        return "\n".join(lines).strip()
    if extension == "txt":
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            try:
                return data.decode(encoding).strip()
            except UnicodeError:
                continue
        raise ValueError("无法识别文本编码，请使用 UTF-8 保存。")
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("加密 PDF 请解密后上传。")
    if len(reader.pages) > settings.max_pdf_pages:
        raise ValueError(f"PDF 最多支持 {settings.max_pdf_pages} 页。")
    import pypdfium2 as pdfium
    pages = []
    with _PDF_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            for index in range(len(reader.pages)):
                page = pdf[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        content, corrupt, watermarks = _positioned_text(textpage, page.get_rotation())
                    finally:
                        textpage.close()
                    if len(content.strip()) < 30 or corrupt:
                        bitmap = page.render(scale=2)
                        try:
                            with bitmap.to_pil() as image:
                                content = image_text(image)
                        finally:
                            bitmap.close()
                        for watermark in watermarks:
                            content = content.replace(watermark, '')
                        if corrupt and (len(content.strip()) < 30 or _corrupt_text(content)):
                            raise ValueError(f"第{index + 1}页文字乱码且OCR未能识别，请检查文件清晰度。")
                    pages.append(content)
                finally:
                    page.close()
        finally:
            pdf.close()
    return "\n".join(pages).strip()
