"""模組名稱：app.services.invoice_intake_service
功能說明：ADMIN 發票上傳流程的協調（W3）——辨識、檢核（必填、業務警示、查重）、提交，以及提交結果不明時的查證。
相依模組：app.integrations.api_intake_client、app.services.invoice_intake_mapping、app.db.repositories.case_repository
注意事項：
    - 不複製 AI 辨識或發票檢核：全部呼叫既有 API；也不寫入資料庫（唯一的 DB 存取是唯讀查證 invoice_id）。
    - 認證（2026-11-03 核定方案 A）：ADMIN 登入成功時用同一組帳密向 API 登入換發 Session Token，
      存在 httpOnly cookie；之後以該使用者自己的 Token 呼叫 API。沒有 Token 時 build_intake_client 拒絕。
    - 檢核結果只是提醒，不阻擋提交（提交與否由 API 決定）；提交逾時的處理見 confirm_submitted。
"""

from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.db.repositories import case_repository as repo
from app.integrations.api_intake_client import (
    ApiIntakeClient,
    BearerCredentials,
    login_for_token,
)
from app.schemas.intake import (
    IntakeFileError,
    IntakeForm,
    IntakeNotConfiguredError,
    RecognitionView,
    SubmitView,
)
from app.services import invoice_intake_mapping as mapping
from app.services.case_service import SessionFactory

MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # 與 API 的上限相同；超過時不送往 API


def obtain_api_token(email: str, password: str) -> str | None:
    """ADMIN 登入成功後，用同一組帳密向 API 換發 Session Token；失敗回 None（不影響 ADMIN 登入）。"""
    return login_for_token(settings.intake_api_base_url, email, password)


def build_intake_client(api_token: str | None) -> ApiIntakeClient:
    """建立呼叫既有 API 的用戶端，授權使用該使用者自己的 API Session Token。

    Raises:
        IntakeNotConfiguredError: 沒有 Token（尚未向 API 登入或已過期）；不使用共用帳號或預設憑證。
    """
    if not api_token:
        raise IntakeNotConfiguredError
    return ApiIntakeClient(settings.intake_api_base_url, BearerCredentials(api_token))


@dataclass(frozen=True)
class CheckOutcome:
    """三項檢核的合併結果（皆已轉成畫面欄位位置）。"""

    required_complete: bool
    missing: dict[str, str]
    warnings: dict[str, list[str]]
    duplicate: bool | None  # None＝資訊不足，未查重


def recognize(
    client: ApiIntakeClient, *, content: bytes, filename: str, content_type: str
) -> tuple[RecognitionView, IntakeForm]:
    """上傳影像給 API 辨識，回傳 API 原始結果與預填表單。

    Raises:
        IntakeFileError: 空檔或超過大小上限（不送往 API）。
        IntakeApiError／IntakeTransportError: 見 api_intake_client。
    """
    if not content:
        raise IntakeFileError("請先選擇發票檔案")
    if len(content) > MAX_UPLOAD_BYTES:
        raise IntakeFileError("發票檔案超過 4 MB，請壓縮或重新拍攝")
    recognition = client.recognize(content, filename or "invoice", content_type)
    return recognition, mapping.form_from_recognition(recognition)


def check(client: ApiIntakeClient, form: IntakeForm) -> CheckOutcome:
    """依目前表單呼叫 API 的必填檢核、業務警示與查重；任何一項失敗就整體失敗，不回傳部分結果。"""
    body = mapping.check_body(form)
    required = client.check_required(body)
    warnings = client.check_warnings(body)
    duplicate_body = mapping.duplicate_body(form)
    duplicate = client.check_duplicate(duplicate_body).is_duplicate if duplicate_body else None
    return CheckOutcome(
        required_complete=required.required_complete,
        missing=mapping.missing_by_field(required.missing),
        warnings=mapping.warnings_by_field(warnings.warnings),
        duplicate=duplicate,
    )


def submit(client: ApiIntakeClient, form: IntakeForm) -> SubmitView:
    """送出表單；重複、逾時、格式錯誤等由 API 判斷並以 IntakeApiError 回報。"""
    return client.submit(mapping.submit_body(form))


def confirm_submitted(
    recognition_id: str, user_id: UUID, *, session_factory: SessionFactory = repo.open_session
) -> UUID | None:
    """提交結果不明（逾時、連線中斷）時，唯讀查證這次辨識是否已成為案件；查不到（或 DB 失敗）回 None。

    回傳 None 不代表一定沒送出（DB 讀取失敗時同樣為 None），呼叫端必須明確告知使用者「尚未確認」。
    """
    try:
        rid = UUID(recognition_id)
    except ValueError:
        return None
    try:
        db = session_factory()
    except Exception:
        return None
    try:
        repo.begin_read_snapshot(db)
        return repo.find_invoice_id_for_recognition(db, rid, user_id)
    except repo.SQLAlchemyError:
        return None
    finally:
        try:
            db.rollback()
        finally:
            db.close()
