"""
模組名稱：tests.unit.test_rag_long_pdf
功能說明：長 PDF 分批辨識——第一批 1～15 頁，滿 15 頁才同時送出其餘各批與第 46 頁探測；
          結尾判斷、超過 45 頁、錯誤代碼，以及合併後文字位置正確（項次可跨批接續）。
相依模組：app.services.rag_indexing_service
注意事項：Document AI 以替身代替，依要求的頁碼回傳假頁面，不呼叫 Google。
"""

import threading

import pytest

from app.integrations.document_ai import (
    DocumentAIError,
    OcrLine,
    OcrPage,
    OcrResult,
    ProcessOptions,
    TextSegment,
)
from app.schemas.rag import ERROR_PDF_PAGE_LIMIT
from app.services.rag_indexing_service import (
    DocumentTextExtractor,
    TextExtractionError,
    merge_ocr_results,
)


class FakePdf:
    """總頁數固定的替身：依 options.pages 回傳存在的頁；全部超出結尾時依設定回空頁或錯誤。"""

    def __init__(
        self,
        total_pages: int,
        *,
        beyond_end_error: bool = False,
        fail_pages: int | None = None,
    ) -> None:
        self.total_pages = total_pages
        self.beyond_end_error = beyond_end_error
        self.fail_pages = fail_pages
        self.requests: list[tuple[int, ...]] = []
        self._lock = threading.Lock()

    def process(
        self,
        content: bytes,
        mime_type: str,
        *,
        timeout_seconds: float | None = None,
        options: ProcessOptions | None = None,
    ) -> OcrResult:
        pages = options.pages if options else ()
        with self._lock:
            self.requests.append(pages)
        if self.fail_pages is not None and self.fail_pages in pages:
            raise DocumentAIError("document_ai_timeout")
        existing = [p for p in pages if p <= self.total_pages]
        if not existing and self.beyond_end_error:
            raise DocumentAIError("document_ai_invalid_input", "page out of range")
        text = "".join(f"第{p}頁內容\n" for p in existing)
        ocr_pages = tuple(OcrPage(p, None, None, None, ()) for p in existing)
        return OcrResult(text, ocr_pages, {})


def _extract(pdf: FakePdf) -> str:
    return (
        DocumentTextExtractor(lambda: pdf).extract(b"%PDF", filename="a.pdf", mime_type=None).text
    )


def test_short_pdf_uses_only_the_first_batch() -> None:
    pdf = FakePdf(3)
    text = _extract(pdf)
    assert pdf.requests == [tuple(range(1, 16))]
    assert text.count("頁內容") == 3


@pytest.mark.parametrize("beyond_end_error", [False, True])
def test_twenty_page_pdf_is_merged_in_order(beyond_end_error: bool) -> None:
    pdf = FakePdf(20, beyond_end_error=beyond_end_error)
    text = _extract(pdf)
    assert sorted(pdf.requests) == sorted(
        [tuple(range(1, 16)), tuple(range(16, 31)), tuple(range(31, 46)), (46,)]
    )
    assert [line for line in text.split("\n") if line] == [f"第{p}頁內容" for p in range(1, 21)]


def test_exactly_forty_five_pages_is_accepted() -> None:
    text = _extract(FakePdf(45))
    assert text.count("頁內容") == 45 and "第45頁內容" in text


@pytest.mark.parametrize("total", [46, 80])
def test_more_than_forty_five_pages_is_rejected(total: int) -> None:
    with pytest.raises(TextExtractionError) as error:
        _extract(FakePdf(total))
    assert error.value.code == ERROR_PDF_PAGE_LIMIT


def test_service_error_in_later_batch_keeps_its_code() -> None:
    with pytest.raises(TextExtractionError) as error:
        _extract(FakePdf(30, fail_pages=20))
    assert error.value.code == "document_ai_timeout"


def test_first_batch_page_limit_detail_maps_to_fixed_code() -> None:
    class TooLong:
        def process(self, *args: object, **kwargs: object) -> OcrResult:
            raise DocumentAIError("document_ai_invalid_input", "PAGE_LIMIT_EXCEEDED")

    with pytest.raises(TextExtractionError) as error:
        DocumentTextExtractor(TooLong).extract(b"%PDF", filename="a.pdf", mime_type=None)
    assert error.value.code == ERROR_PDF_PAGE_LIMIT


def test_merge_shifts_line_positions_of_later_batches() -> None:
    first_text = "第一頁\n"
    second_text = "2 營業人應依規定\n"
    line = OcrLine("2 營業人應依規定", None, ((0.1, 0.1),), (TextSegment(0, 9),))
    first = OcrResult(first_text, (OcrPage(1, None, None, None, ()),), {})
    second = OcrResult(second_text, (OcrPage(16, None, None, None, (line,)),), {})

    merged = merge_ocr_results([first, second])

    assert merged.text == first_text + second_text
    segment = merged.pages[1].lines[0].text_segments[0]
    assert merged.text[segment.start : segment.end] == "2 營業人應依規定"


def test_merge_adds_newline_between_batches_when_missing() -> None:
    merged = merge_ocr_results([OcrResult("甲", (), {}), OcrResult("乙", (), {})])
    assert merged.text == "甲\n乙"
