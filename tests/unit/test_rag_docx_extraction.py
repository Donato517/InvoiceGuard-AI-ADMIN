"""
模組名稱：tests.unit.test_rag_docx_extraction
功能說明：DOCX 文字擷取——段落、表格、分行與定位、修訂刪除不取、壞檔與過大檔拒絕；以記憶體內合成的 DOCX 測試。
相依模組：app.services.rag_indexing_service
"""

import io
import zipfile

import pytest

from app.schemas.rag import ERROR_DOCX_INVALID
from app.services import rag_indexing_service as indexing
from app.services.rag_indexing_service import (
    DOCX_MIME_TYPE,
    DocumentTextExtractor,
    TextExtractionError,
    extract_docx_text,
)

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(body: str, *, name: str = "word/document.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, f"<w:document {NS}><w:body>{body}</w:body></w:document>")
    return buffer.getvalue()


def _p(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def _t(text: str) -> str:
    return f"<w:r><w:t>{text}</w:t></w:r>"


def test_paragraphs_tables_tabs_and_breaks_in_document_order() -> None:
    body = (
        _p(_t("第一條 總則"))
        + _p(_t("營業人"), "<w:r><w:tab/></w:r>", _t("應依規定"), "<w:r><w:br/></w:r>", _t("申報"))
        + "<w:tbl><w:tr>"
        + "<w:tc>"
        + _p(_t("項目"))
        + "</w:tc><w:tc>"
        + _p(_t("說明"))
        + "</w:tc>"
        + "</w:tr><w:tr>"
        + "<w:tc>"
        + _p(_t("稅率"))
        + "</w:tc><w:tc>"
        + _p(_t("百分之五"))
        + "</w:tc>"
        + "</w:tr></w:tbl>"
        + "<w:sdt><w:sdtContent>"
        + _p(_t("第二條 附則"))
        + "</w:sdtContent></w:sdt>"
    )

    text = extract_docx_text(_docx(body))

    assert text.split("\n") == [
        "第一條 總則",
        "營業人\t應依規定",
        "申報",
        "項目\t說明",
        "稅率\t百分之五",
        "第二條 附則",
    ]


def test_deleted_revision_text_is_not_extracted() -> None:
    body = _p(_t("保留"), "<w:del><w:r><w:delText>刪除的字</w:delText></w:r></w:del>")
    assert extract_docx_text(_docx(body)) == "保留"


@pytest.mark.parametrize(
    "content",
    [
        b"not a zip",
        _docx(_p(_t("x")), name="word/other.xml"),
        b"PK\x03\x04broken",
    ],
)
def test_invalid_docx_is_rejected(content: bytes) -> None:
    with pytest.raises(TextExtractionError) as error:
        extract_docx_text(content)
    assert error.value.code == ERROR_DOCX_INVALID


def test_oversized_document_xml_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexing, "DOCX_MAX_XML_BYTES", 10)
    with pytest.raises(TextExtractionError) as error:
        extract_docx_text(_docx(_p(_t("超過上限的內容"))))
    assert error.value.code == ERROR_DOCX_INVALID


@pytest.mark.parametrize(
    "filename,mime_type",
    [("規章.docx", None), ("規章", DOCX_MIME_TYPE), ("規章.DOCX", "application/octet-stream")],
)
def test_extractor_routes_docx_without_document_ai(filename: str, mime_type: str | None) -> None:
    def no_pdf_client() -> None:
        pytest.fail("DOCX 不可呼叫 Document AI")

    extractor = DocumentTextExtractor(no_pdf_client)
    extracted = extractor.extract(_docx(_p(_t("第一點"))), filename=filename, mime_type=mime_type)
    assert extracted.pages[0].text == "第一點"
