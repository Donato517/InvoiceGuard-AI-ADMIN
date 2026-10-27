"""模組名稱：tests.unit.test_dev_files；功能說明：開發者檔案頁（Supabase Storage S3 用戶端、service、路由）測試；全部使用合成資料與替身，不連網路。"""

import hashlib
import io
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from email.message import Message
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import deps
from app.core.config import Settings
from app.integrations import supabase_storage
from app.services import dev_file_service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SECRET = "SYNTHETIC-SECRET-ACCESS-KEY"


def make_settings(**values: Any) -> Settings:
    """以隔離前綴與合成值建構，不讀取公司環境變數。"""
    return Settings(_env_file=None, _env_prefix="IG_UNIT_TEST_", **values)


LOCAL = make_settings()
CLOUD = make_settings(VERCEL="1", VERCEL_ENV="production", APP_ENV="production")


# ---- SigV4 ----


def test_sign_v4_matches_aws_reference_vector() -> None:
    """AWS SigV4 測試套件 get-vanilla。"""
    headers = supabase_storage.sign_v4(
        "GET",
        "example.amazonaws.com",
        "/",
        {},
        {},
        hashlib.sha256(b"").hexdigest(),
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="service",
        now=datetime(2015, 8, 30, 12, 36, tzinfo=UTC),
    )
    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


# ---- S3 用戶端 ----


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status, self.body = status, body
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self.body


class FakeOpener:
    """記錄請求並依序回傳預設回應；回應為 (status, body) 或 (status, body, headers)。"""

    def __init__(self, *responses: tuple[Any, ...]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float) -> FakeResponse:
        self.requests.append(request)
        status, body, *rest = self.responses.pop(0)
        headers = rest[0] if rest else {}
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "x", Message(), io.BytesIO(body))
        return FakeResponse(status, body, headers)


def client(opener: FakeOpener) -> supabase_storage.S3StorageClient:
    return supabase_storage.S3StorageClient(
        endpoint_url="https://ref.storage.example.com/storage/v1/s3",
        region="synthetic-1",
        access_key_id="SYNTHETICACCESS",
        secret_access_key=SECRET,
        bucket="bucket",
        opener=opener,  # type: ignore[arg-type]
    )


def test_put_signs_path_style_request_with_payload_hash() -> None:
    opener = FakeOpener((200, b""))
    client(opener).put("dev/images/a.png", PNG, content_type="image/png")
    request = opener.requests[0]
    assert request.get_method() == "PUT"
    assert (
        request.full_url == "https://ref.storage.example.com/storage/v1/s3/bucket/dev/images/a.png"
    )
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["x-amz-content-sha256"] == hashlib.sha256(PNG).hexdigest()
    assert headers["content-type"] == "image/png"
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=SYNTHETICACCESS/")
    assert "/synthetic-1/s3/aws4_request" in headers["authorization"]
    assert "content-type;host;x-amz-content-sha256;x-amz-date" in headers["authorization"]
    assert SECRET not in str(request.header_items())
    assert request.data == PNG


def test_list_objects_follows_continuation_and_parses_xml() -> None:
    namespace = "http://s3.amazonaws.com/doc/2006-03-01/"

    def page(keys: list[str], token: str | None) -> bytes:
        items = "".join(
            f"<Contents><Key>{key}</Key><Size>3</Size>"
            "<LastModified>2026-10-25T07:00:00.000Z</LastModified></Contents>"
            for key in keys
        )
        more = (
            f"<IsTruncated>true</IsTruncated><NextContinuationToken>{token}</NextContinuationToken>"
            if token
            else "<IsTruncated>false</IsTruncated>"
        )
        return f'<ListBucketResult xmlns="{namespace}">{items}{more}</ListBucketResult>'.encode()

    opener = FakeOpener((200, page(["dev/rag/b.txt"], "T1")), (200, page(["dev/rag/a.txt"], None)))
    objects = client(opener).list_objects("dev/rag/")
    assert [stored.key for stored in objects] == ["dev/rag/a.txt", "dev/rag/b.txt"]
    assert objects[0].size_bytes == 3 and objects[0].last_modified.tzinfo is not None
    first, second = (urllib.parse.urlsplit(r.full_url) for r in opener.requests)
    assert first.path == "/storage/v1/s3/bucket"
    assert urllib.parse.parse_qs(first.query) == {"list-type": ["2"], "prefix": ["dev/rag/"]}
    assert urllib.parse.parse_qs(second.query)["continuation-token"] == ["T1"]


def test_get_move_and_delete() -> None:
    opener = FakeOpener(
        (200, b"hello", {"Content-Type": "text/plain; charset=utf-8"}),
        (200, b"<CopyObjectResult></CopyObjectResult>"),
        (204, b""),
    )
    storage = client(opener)
    assert storage.get("dev/rag/a.txt") == (b"hello", "text/plain; charset=utf-8")
    storage.move("dev/rag/a.txt", "dev/rag/archive/a.txt")
    get, copy, delete = opener.requests
    assert copy.get_method() == "PUT"
    assert copy.full_url.endswith("/bucket/dev/rag/archive/a.txt")
    assert dict(copy.header_items())["X-amz-copy-source"] == "bucket/dev/rag/a.txt"
    assert (delete.get_method(), delete.full_url.endswith("/bucket/dev/rag/a.txt")) == (
        "DELETE",
        True,
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (403, "storage_denied"),
        (404, "storage_not_found"),
        (415, "storage_type_rejected"),
        (500, "storage_failed"),
    ],
)
def test_http_errors_become_fixed_codes_with_s3_code_only(status: int, code: str) -> None:
    body = b"<Error><Code>NoSuchKey</Code><Message>secret detail</Message></Error>"
    with pytest.raises(supabase_storage.StorageError) as caught:
        client(FakeOpener((status, body))).get("dev/rag/a.txt")
    assert (caught.value.code, caught.value.detail) == (code, "NoSuchKey")


def test_failed_copy_does_not_delete_source() -> None:
    opener = FakeOpener((200, b"<Error><Code>InternalError</Code></Error>"))
    with pytest.raises(supabase_storage.StorageError):
        client(opener).move("dev/rag/a.txt", "dev/rag/b/a.txt")
    assert len(opener.requests) == 1


def test_network_error_is_unreachable() -> None:
    class Broken:
        def open(self, request: Any, timeout: float) -> Any:
            raise urllib.error.URLError("synthetic")

    storage = client(Broken())  # type: ignore[arg-type]
    with pytest.raises(supabase_storage.StorageError) as caught:
        storage.delete("dev/rag/a.txt")
    assert caught.value.code == "storage_unreachable"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"S3_ENDPOINT_URL": "http://ref.example.com/s3"},
        {"S3_ENDPOINT_URL": "https://ref.example.com/s3", "S3_REGION": "r"},
    ],
)
def test_missing_settings_are_not_configured(values: dict[str, str]) -> None:
    with pytest.raises(supabase_storage.StorageError) as caught:
        supabase_storage.S3StorageClient.from_settings(make_settings(**values))
    assert caught.value.code == "storage_not_configured"


def test_from_settings_uses_s3_variables_and_bucket() -> None:
    configured = make_settings(
        S3_ENDPOINT_URL="https://ref.example.com/storage/v1/s3",
        S3_REGION="synthetic-1",
        S3_ACCESS_KEY_ID="A",
        S3_SECRET_ACCESS_KEY="B",
        SUPABASE_STORAGE_BUCKET="bucket",
    )
    assert isinstance(
        supabase_storage.S3StorageClient.from_settings(configured),
        supabase_storage.S3StorageClient,
    )


# ---- service ----


class MemoryStorage:
    """記憶體 Storage 替身。"""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    def get(self, key: str) -> tuple[bytes, str]:
        if key not in self.objects:
            raise supabase_storage.StorageError("storage_not_found", "NoSuchKey")
        return self.objects[key]

    def list_objects(self, prefix: str) -> list[supabase_storage.StoredObject]:
        when = datetime(2026, 10, 25, tzinfo=UTC)
        return [
            supabase_storage.StoredObject(key, len(data), when)
            for key, (data, _) in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def move(self, source_key: str, destination_key: str) -> None:
        self.objects[destination_key] = self.get(source_key)
        del self.objects[source_key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> MemoryStorage:
    fake = MemoryStorage()
    monkeypatch.setattr(
        supabase_storage.S3StorageClient, "from_settings", classmethod(lambda cls: fake)
    )
    return fake


def test_two_categories_use_different_storage_paths(storage: MemoryStorage) -> None:
    image = dev_file_service.upload("images", "我的 Photo!.PNG", PNG)
    text = dev_file_service.upload("rag", "notes.md", "# 標題\n內容".encode())
    assert image.endswith("_Photo.png") and text.endswith("_notes.md")
    assert set(storage.objects) == {f"dev/images/{image}", f"dev/rag/{text}"}
    assert storage.objects[f"dev/images/{image}"][1] == "image/png"
    assert storage.objects[f"dev/rag/{text}"][1] == "text/markdown"
    assert [entry.name for entry in dev_file_service.list_files("images")] == [image]
    assert [entry.name for entry in dev_file_service.list_files("rag")] == [text]


@pytest.mark.parametrize(
    "category,filename,data,code",
    [
        ("images", "a.png", b"", "file_required"),
        ("images", "a.exe", PNG, "unsupported_type"),
        ("images", "a.png", b"not a png", "content_mismatch"),
        ("images", "a.webp", b"RIFF0000WEBP", "unsupported_type"),
        ("images", "a.gif", b"GIF89a", "unsupported_type"),
        ("rag", "a.png", PNG, "unsupported_type"),
        ("rag", "a.txt", b"\xff\xfe\x00bad", "text_not_utf8"),
        pytest.param(
            "rag",
            "a.txt",
            b"x" * (dev_file_service.MAX_BYTES + 1),
            "file_too_large",
            id="file_too_large",
        ),
        ("other", "a.txt", b"x", "unknown_category"),
    ],
)
def test_upload_rejects_bad_files_before_storage(
    storage: MemoryStorage, category: str, filename: str, data: bytes, code: str
) -> None:
    with pytest.raises(dev_file_service.DevFileError) as caught:
        dev_file_service.upload(category, filename, data)
    assert caught.value.code == code
    assert storage.objects == {}


def test_download_move_and_delete_stay_inside_category(storage: MemoryStorage) -> None:
    name = dev_file_service.upload("rag", "doc.txt", b"hello")
    assert dev_file_service.download("rag", name) == (b"hello", "text/plain", name)
    moved = dev_file_service.move("rag", name, "archive/2026")
    assert moved == f"archive/2026/{name}"
    assert set(storage.objects) == {f"dev/rag/archive/2026/{name}"}
    back = dev_file_service.move("rag", moved, "")
    assert back == name
    with pytest.raises(dev_file_service.DevFileError) as same:
        dev_file_service.move("rag", name, "")
    assert same.value.code == "same_location"
    dev_file_service.delete("rag", name)
    assert storage.objects == {}


@pytest.mark.parametrize("name", ["../x.txt", "/abs.txt", "a/../b.txt", "a\\b.txt", ".hidden", ""])
def test_names_cannot_escape_category(storage: MemoryStorage, name: str) -> None:
    for operation in (
        lambda: dev_file_service.download("rag", name),
        lambda: dev_file_service.delete("rag", name),
        lambda: dev_file_service.move("rag", name, "archive"),
    ):
        with pytest.raises(dev_file_service.DevFileError) as caught:
            operation()
        assert caught.value.code == "invalid_name"


@pytest.mark.parametrize("folder", ["../up", "a b", "a//b", "中文"])
def test_move_rejects_bad_folders(storage: MemoryStorage, folder: str) -> None:
    name = dev_file_service.upload("rag", "a.txt", b"x")
    with pytest.raises(dev_file_service.DevFileError) as caught:
        dev_file_service.move("rag", name, folder)
    assert caught.value.code == "invalid_folder"


def test_storage_errors_keep_fixed_message_and_s3_code(storage: MemoryStorage) -> None:
    with pytest.raises(dev_file_service.DevFileError) as caught:
        dev_file_service.download("rag", "missing.txt")
    assert caught.value.code == "storage_not_found"
    assert str(caught.value) == dev_file_service.MESSAGES["storage_not_found"] + "（NoSuchKey）"


def test_without_s3_settings_reports_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase_storage, "settings", make_settings())
    with pytest.raises(dev_file_service.DevFileError) as caught:
        dev_file_service.list_files("images")
    assert caught.value.code == "storage_not_configured"


# ---- 路由 ----


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "settings", LOCAL)


def test_page_is_404_in_cloud(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "settings", CLOUD)
    assert client.get("/dev/files").status_code == 404


@pytest.mark.usefixtures("local")
def test_page_shows_two_upload_forms_with_their_paths(
    client: TestClient, storage: MemoryStorage
) -> None:
    dev_file_service.upload("images", "a.png", PNG)
    page = client.get("/dev/files")
    assert page.status_code == 200
    assert 'action="/dev/files/images/upload"' in page.text
    assert 'action="/dev/files/rag/upload"' in page.text
    assert "dev/images/" in page.text and "dev/rag/" in page.text
    assert 'enctype="multipart/form-data"' in page.text
    assert "_a.png" in page.text and "此路徑目前沒有檔案" in page.text


@pytest.mark.usefixtures("local")
def test_upload_download_move_delete_through_routes(
    client: TestClient, storage: MemoryStorage
) -> None:
    uploaded = client.post(
        "/dev/files/rag/upload", files={"file": ("guide.txt", b"hello", "text/plain")}
    )
    assert uploaded.status_code == 200 and "location" not in uploaded.headers
    (key,) = storage.objects
    name = key.removeprefix("dev/rag/")
    assert f"已上傳：{name}" in uploaded.text

    downloaded = client.get("/dev/files/rag/download", params={"name": name})
    assert downloaded.status_code == 200 and downloaded.content == b"hello"
    assert downloaded.headers["content-disposition"] == f"attachment; filename*=UTF-8''{name}"

    moved = client.post("/dev/files/rag/move", data={"name": name, "folder": "archive"})
    assert moved.status_code == 200 and "location" not in moved.headers
    assert f"已移動：archive/{name}" in moved.text
    assert set(storage.objects) == {f"dev/rag/archive/{name}"}

    deleted = client.post("/dev/files/rag/delete", data={"name": f"archive/{name}"})
    assert deleted.status_code == 200 and "location" not in deleted.headers
    assert f"已刪除：archive/{name}" in deleted.text and storage.objects == {}


@pytest.mark.usefixtures("local")
def test_page_submits_in_place_and_ignores_messages_in_url(
    client: TestClient, storage: MemoryStorage
) -> None:
    page = client.get("/dev/files", params={"saved": "uploaded", "category": "rag", "name": "x"})
    assert page.status_code == 200 and "已上傳" not in page.text
    assert 'hx-target="#dev-files"' in page.text and 'hx-select="#dev-files"' in page.text
    for key in ("images", "rag"):
        assert f'hx-post="/dev/files/{key}/upload"' in page.text


@pytest.mark.usefixtures("local")
def test_upload_error_rerenders_with_message(client: TestClient, storage: MemoryStorage) -> None:
    response = client.post(
        "/dev/files/images/upload", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400
    assert dev_file_service.MESSAGES["unsupported_type"] in response.text
    assert storage.objects == {}


@pytest.mark.usefixtures("local")
def test_cross_site_writes_are_rejected(client: TestClient, storage: MemoryStorage) -> None:
    evil = {"Origin": "https://evil.example.com"}
    upload = client.post(
        "/dev/files/rag/upload", files={"file": ("a.txt", b"x", "text/plain")}, headers=evil
    )
    delete = client.post("/dev/files/rag/delete", data={"name": "a.txt"}, headers=evil)
    assert (upload.status_code, delete.status_code) == (403, 403)
    assert storage.objects == {}
