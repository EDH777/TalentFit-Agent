from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services import extraction


def pdf_bytes(operations: str, unicode_map: str | None = None, rotation: int = 0) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(600, 840)
    if rotation:
        page.rotate(rotation)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    if unicode_map:
        cmap = DecodedStreamObject()
        cmap.set_data(unicode_map.encode())
        font[NameObject('/ToUnicode')] = writer._add_object(cmap)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(operations.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def line(text: str, x=50, y=750, size=12) -> str:
    return f'BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET\n'


def test_pdf_follows_page_positions_not_content_stream_order():
    data = pdf_bytes(line('Work experience at Example Company', y=400)
                     + line('Synthetic Candidate', y=780, size=20)
                     + line('Skills: Java Spring SQL', y=600))
    result = extraction.extract_text('resume.pdf', data)
    assert result.index('Synthetic Candidate') < result.index('Skills:') < result.index('Work experience')


def test_repeated_rotated_tracking_watermark_is_removed_but_real_identifiers_remain():
    watermark = 'TrackingMarker0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    identifier = '0123456789abcdef' * 4
    body = line('Synthetic Candidate: Skills and work experience', y=790)
    body += ''.join(line(identifier, y=y) for y in [700, 650, 600])
    body += line(watermark, y=30)
    for y in [100, 250, 400]:
        body += f'q 0.766044 0.642788 -0.642788 0.766044 150 {y} cm {line(watermark, x=0, y=0)} Q\n'
    result = extraction.extract_text('resume.pdf', pdf_bytes(body))
    assert watermark not in result
    assert result.count(identifier) == 3
    assert 'Skills and work experience' in result


def test_private_glyphs_trigger_ocr_even_when_text_layer_is_long(monkeypatch):
    cmap = '/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Test def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <E001> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
    data = pdf_bytes(line('A' * 80), unicode_map=cmap)
    calls = []
    monkeypatch.setattr(extraction, 'image_text', lambda image: calls.append(image.size) or '恢复后的合成简历文字，工作经历、教育经历与项目经验均已正确识别，具有足够长度。')
    result = extraction.extract_text('broken-font.pdf', data)
    assert calls and '恢复后的合成简历文字' in result and '\ue001' not in result


def test_failed_ocr_does_not_accept_a_long_corrupt_text_layer(monkeypatch):
    cmap = '/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Test def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <E001> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
    monkeypatch.setattr(extraction, 'image_text', lambda image: '')
    with pytest.raises(ValueError, match='识别|乱码'):
        extraction.extract_text('broken-font.pdf', pdf_bytes(line('A' * 80), unicode_map=cmap))


def test_positioned_text_preserves_programming_tokens_and_punctuation(monkeypatch):
    def unexpected_ocr(image):
        pytest.fail('A healthy digital PDF must not be OCRed')
    monkeypatch.setattr(extraction, 'image_text', unexpected_ocr)
    text = 'Skills: C++ C# .NET UTF-8 JavaScript TypeScript FastAPI'
    assert text in extraction.extract_text('english.pdf', pdf_bytes(line(text)))


def test_image_only_pdf_uses_existing_ocr(monkeypatch):
    monkeypatch.setattr(extraction, 'image_text', lambda image: '合成扫描简历：工作经历包括软件开发和系统维护，教育经历为计算机专业，项目经验完整。')
    assert '合成扫描简历' in extraction.extract_text('scanned.pdf', pdf_bytes(''))


def test_ocr_does_not_reintroduce_identified_tracking_watermarks(monkeypatch):
    cmap = '/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Test def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <E001> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
    watermark = 'tracking-marker-0123456789-abcdefghijklmnopqrstuvwxyz'
    body = line('A' * 80)
    for y in [100, 250, 400]:
        body += f'q 0.766044 0.642788 -0.642788 0.766044 150 {y} cm {line(watermark, x=0, y=0)} Q\n'
    monkeypatch.setattr(extraction, 'image_text', lambda image: '合成简历的工作经历、教育经历、项目经验和专业技能均已识别，完整正文应当保留。\n' + watermark)
    result = extraction.extract_text('watermarked-corrupt.pdf', pdf_bytes(body, unicode_map=cmap))
    assert watermark not in result and '完整正文应当保留' in result


def test_isolated_private_use_bullets_do_not_force_healthy_text_through_ocr(monkeypatch):
    cmap = '/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Test def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <F0B7> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
    body = ''.join(line('A Work experience: maintained backend services.', y=y) for y in [750, 700, 650, 600])
    def unexpected_ocr(image):
        pytest.fail('Isolated decorative bullets must not force OCR')
    monkeypatch.setattr(extraction, 'image_text', unexpected_ocr)
    result = extraction.extract_text('bullets.pdf', pdf_bytes(body, unicode_map=cmap))
    assert result.count('Work experience: maintained backend services.') == 4


@pytest.mark.parametrize('angle,matrix', [(90, '0 1 -1 0 600 0'), (180, '-1 0 0 -1 600 840'), (270, '0 -1 1 0 0 840')])
def test_display_rotation_is_applied_before_reading_order(angle, matrix):
    body = f'q {matrix} cm ' + line('Work experience at Example Company', y=200) + line('Synthetic Candidate and skills', y=500) + ' Q'
    result = extraction.extract_text('rotated.pdf', pdf_bytes(body, rotation=angle))
    assert 'Synthetic Candidate and skills' in result
    assert result.index('Synthetic Candidate and skills') < result.index('Work experience at Example Company')
