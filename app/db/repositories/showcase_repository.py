"""模組名稱：app.db.repositories.showcase_repository；功能說明：部門隔離、CAS 與歷程固定 SQL。

共用 Schema 由 API migration 管理；本模組不 commit，不存取任意表名。
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.repositories import showcase_v2_reader as v2
from app.schemas.e_admin import ConflictError, MissingError
from app.schemas.showcase import SearchInput

StoreError = SQLAlchemyError

EXAMPLE_COLUMNS = (
    "example_name",
    "template_key",
    "bucket_name",
    "storage_path",
    "original_filename",
    "mime_type",
    "size_bytes",
    "content_sha256",
    "invoice_track",
    "invoice_number",
    "invoice_date",
    "tax_period",
    "seller_name",
    "buyer_tax_id",
    "buyer_name",
    "sales_amount",
    "tax_type",
    "tax_amount",
    "total_amount",
    "total_amount_zh",
    "notes",
    "status",
    "verified_field_keys",
    "verified_by",
    "verified_at",
    "verification_note",
    "updated_by",
)


def save_example(
    db: Session, header: dict[str, Any], items: list[dict[str, Any]], expected_version: int | None
) -> int:
    """鎖定於範例交易內，保存 v2.0 表頭及有列號的明細。"""
    fields = ", ".join(
        ("example_id", "supplier_id", "seller_tax_id", "created_by", *EXAMPLE_COLUMNS)
    )
    assignments = ", ".join(f"{column}=EXCLUDED.{column}" for column in EXAMPLE_COLUMNS)
    version = db.execute(
        text(f"""
        INSERT INTO public.supplier_invoice_examples AS stored ({fields})
        SELECT {fields}
        FROM jsonb_populate_record(NULL::public.supplier_invoice_examples, CAST(:header AS jsonb)) AS input
        WHERE CAST(:expected AS integer) IS NULL OR EXISTS (
          SELECT 1 FROM public.supplier_invoice_examples e WHERE e.example_id=input.example_id
        )
        ON CONFLICT (example_id) DO UPDATE SET {assignments},
          example_version=stored.example_version + 1
        WHERE stored.supplier_id=EXCLUDED.supplier_id AND stored.seller_tax_id=EXCLUDED.seller_tax_id
          AND stored.example_version=:expected
          AND stored.source_invoice_id IS NULL AND stored.source_file_id IS NULL
          AND stored.source_recognition_id IS NULL
        RETURNING example_version
        """),
        {"header": json.dumps(header, ensure_ascii=False), "expected": expected_version},
    ).scalar_one_or_none()
    if version is None:
        raise ConflictError
    item_params = {"id": UUID(header["example_id"]), "items": json.dumps(items, ensure_ascii=False)}
    db.execute(
        text("""
            DELETE FROM public.supplier_invoice_example_items AS stored
            WHERE stored.example_id=:id AND NOT EXISTS (
                SELECT 1 FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS i(line_no integer)
                WHERE i.line_no=stored.line_no
            )
            """),
        item_params,
    )
    db.execute(
        text("""
            INSERT INTO public.supplier_invoice_example_items
              (example_id,line_no,item_name,quantity,unit_price,line_amount)
            SELECT :id,line_no,item_name,quantity,unit_price,line_amount
            FROM jsonb_to_recordset(CAST(:items AS jsonb))
              AS i(line_no integer,item_name text,quantity numeric,unit_price numeric,line_amount numeric)
            ON CONFLICT (example_id,line_no) DO UPDATE SET
              item_name=EXCLUDED.item_name, quantity=EXCLUDED.quantity,
              unit_price=EXCLUDED.unit_price, line_amount=EXCLUDED.line_amount
            """),
        item_params,
    )
    return version


def set_example_status(
    db: Session,
    sample: UUID,
    version: int,
    status: str,
    actor: UUID,
    reason: str,
    verified_keys: list[str],
) -> None:
    """狀態操作以 v2.0 版本比對，撤回時保留原驗證紀錄。"""
    count = db.execute(
        text("""
        UPDATE public.supplier_invoice_examples SET status=:status, updated_by=:actor,
          verification_note=:note,
          verified_by=CASE WHEN :status='verified' THEN :actor ELSE verified_by END,
          verified_at=CASE WHEN :status='verified' THEN clock_timestamp() ELSE verified_at END,
          verified_field_keys=CASE WHEN :status='verified' THEN CAST(:keys AS text[]) ELSE verified_field_keys END
        WHERE example_id=:id AND example_version=:version
          AND source_invoice_id IS NULL AND source_file_id IS NULL AND source_recognition_id IS NULL
        """),
        {
            "id": sample,
            "version": version,
            "status": status,
            "actor": actor,
            "note": reason,
            "keys": verified_keys,
        },
    ).rowcount
    if count != 1:
        raise ConflictError


def collections(db: Session, department: UUID, term: str, page: int) -> list[dict[str, Any]]:
    """依供應商集中計數，包含完整範例集而非當頁張數。"""
    return [
        dict(row)
        for row in db.execute(
            text("""
        SELECT p.id,p.name,p.tax_id,count(s.id) AS sample_count,
          count(s.id) FILTER (WHERE s.payload->>'active_revision' IS NOT NULL) AS active_count
        FROM invoiceguard_showcase.suppliers p
        LEFT JOIN invoiceguard_showcase.samples s
          ON s.department_id=p.department_id AND s.payload->>'supplier_id'=CAST(p.id AS text)
        WHERE p.department_id=:dept
          AND (strpos(lower(p.name),lower(:q))>0 OR strpos(p.tax_id,:q)>0)
        GROUP BY p.id,p.name,p.tax_id ORDER BY p.name,p.id LIMIT 20 OFFSET :offset
    """),
            {"dept": department, "q": term, "offset": (page - 1) * 20},
        ).mappings()
    ]


def collection(db: Session, department: UUID, supplier_id: UUID) -> dict[str, Any]:
    """固定供應商資源與完整張數均受部門限制。"""
    row = (
        db.execute(
            text("""
        SELECT p.id,p.name,p.tax_id,count(s.id) AS sample_count,
          count(s.id) FILTER (WHERE s.payload->>'active_revision' IS NOT NULL) AS active_count
        FROM invoiceguard_showcase.suppliers p
        LEFT JOIN invoiceguard_showcase.samples s
          ON s.department_id=p.department_id AND s.payload->>'supplier_id'=CAST(p.id AS text)
        WHERE p.department_id=:dept AND p.id=:supplier
        GROUP BY p.id,p.name,p.tax_id
    """),
            {"dept": department, "supplier": supplier_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise MissingError
    return dict(row)


def reference_candidates(
    db: Session, tax_id: str, layout: str = "", *, master_supplier_id: str = ""
) -> list[dict[str, Any]]:
    """依已啟用修訂的賣方統編檢索；絕不回傳新草稿或操作歷史。"""
    return v2.candidates(db, tax_id, layout, master_supplier_id)


def reference_revision(db: Session, sample: UUID, revision: UUID) -> dict[str, Any]:
    """原件每次讀取再次檢查該修訂仍啟用，停用或被取代後不再共享。"""
    row = v2.get_example(db, sample, verified_only=True)
    if row is None or row["id"] != str(revision):
        raise MissingError
    return row


def linked_source_matches(db: Session, sample: UUID, version: int) -> bool:
    """驗證帶正式檔案／辨識來源的範例屬於同一張發票及檔案版本。"""
    return db.execute(
        text("""
        SELECT EXISTS (
            SELECT 1 FROM public.supplier_invoice_examples e
            JOIN public.invoice_files f ON f.file_id=e.source_file_id
            WHERE e.example_id=:sample AND e.example_version=:version
              AND f.invoice_id=e.source_invoice_id AND f.version_no=e.source_file_version
              AND (e.source_recognition_id IS NULL OR EXISTS (
                  SELECT 1 FROM public.ai_recognition_logs r
                  WHERE r.recognition_id=e.source_recognition_id AND r.invoice_file_id=f.file_id
                    AND (r.invoice_id IS NULL OR r.invoice_id=f.invoice_id)
              ))
        )
        """),
        {"sample": sample, "version": version},
    ).scalar_one()


def archive_available(db: Session) -> bool:
    """以唯讀系統目錄判定選用歷程表是否可讀。"""
    return db.execute(
        text("""
        SELECT count(*)=3 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='invoiceguard_showcase' AND c.relname IN ('samples','events','tests')
          AND pg_catalog.has_schema_privilege(n.oid,'USAGE')
          AND pg_catalog.has_table_privilege(c.oid,'SELECT')
        """)
    ).scalar_one()


def archive_writable(db: Session) -> bool:
    """確認建立原圖登錄、版本快照與事件所需的歷程權限。"""
    return db.execute(
        text("""
        SELECT count(*)=3 AND COALESCE(bool_and(
            pg_catalog.has_schema_privilege(n.oid,'USAGE')
            AND pg_catalog.has_table_privilege(c.oid,'SELECT')
            AND pg_catalog.has_table_privilege(c.oid,'INSERT')
            AND (c.relname='events' OR pg_catalog.has_table_privilege(c.oid,'UPDATE'))
        ), false)
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='invoiceguard_showcase' AND c.relname IN ('samples','events','uploads')
        """)
    ).scalar_one()


def search(
    db: Session,
    department: UUID,
    query: str,
    page: int,
    filters: SearchInput | None = None,
    *,
    shared_master: bool = False,
) -> list[dict[str, Any]]:
    """每頁二十筆；共用模式只查指定主檔供應商。"""
    return [
        dict(row)
        for row in db.execute(
            text("""
        SELECT id, version, payload FROM invoiceguard_showcase.samples
        WHERE ((NOT :shared AND department_id=:dept)
               OR (:shared AND payload->>'master_supplier_id'=:supplier))
        AND (strpos(lower(payload->>'title'),lower(:q))>0
          OR strpos(lower(payload->>'supplier_name'),lower(:q))>0
          OR strpos(payload->>'tax_id',:q)>0)
        AND (:layout='' OR strpos(lower(payload->>'layout'),lower(:layout))>0)
        AND (:writing='' OR strpos(lower(payload->'revisions'->-1->>'writing'),lower(:writing))>0)
        AND (:status='all' OR (:status='active' AND payload->>'active_revision' IS NOT NULL)
             OR (:status='inactive' AND payload->>'active_revision' IS NULL))
        AND (:kind='all' OR payload->'revisions'->-1->>'kind'=:kind)
        AND (:supplier='' OR payload->>'supplier_id'=:supplier)
        ORDER BY updated_at DESC,id LIMIT 20 OFFSET :offset
    """),
            {
                "dept": department,
                "q": query,
                "offset": (page - 1) * 20,
                "layout": filters.layout if filters else "",
                "writing": filters.writing if filters else "",
                "status": filters.status if filters else "all",
                "kind": filters.kind if filters else "all",
                "supplier": str(filters.supplier_id) if filters and filters.supplier_id else "",
                "shared": shared_master,
            },
        ).mappings()
    ]


def suppliers(db: Session, department: UUID, term: str) -> list[dict[str, Any]]:
    """搜尋目前部門既有供應商識別，供表單選取，不合併異名。"""
    return [
        dict(row)
        for row in db.execute(
            text("""
        SELECT id,tax_id,name FROM invoiceguard_showcase.suppliers
        WHERE department_id=:dept AND (strpos(lower(name),lower(:q))>0 OR strpos(tax_id,:q)>0)
        ORDER BY name,id LIMIT 30
    """),
            {"dept": department, "q": term},
        ).mappings()
    ]


def read(
    db: Session, department: UUID, sample: UUID, *, lock: bool = False, shared_master: bool = False
) -> dict[str, Any]:
    """跨部門只讀已關聯供應商主檔的範例。"""
    sql = """
        SELECT id,department_id,version,payload FROM invoiceguard_showcase.samples
        WHERE id=:id AND (department_id=:dept OR (:shared AND payload->>'master_supplier_id' IS NOT NULL))
    """
    row = (
        db.execute(
            text(sql + (" FOR UPDATE" if lock else "")),
            {"id": sample, "dept": department, "shared": shared_master},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise MissingError
    return dict(row)


def supplier(db: Session, department: UUID, supplier_id: UUID, tax_id: str, name: str) -> None:
    """建立範例專用供應商識別；不自動更名既有供應商。"""
    db.execute(
        text("""
        INSERT INTO invoiceguard_showcase.suppliers(id,department_id,tax_id,name)
        VALUES(:id,:dept,:tax,:name) ON CONFLICT(department_id,tax_id) DO NOTHING
    """),
        {"id": supplier_id, "dept": department, "tax": tax_id, "name": name},
    )
    current = db.execute(
        text(
            "SELECT name FROM invoiceguard_showcase.suppliers WHERE id=:id AND department_id=:dept"
        ),
        {"id": supplier_id, "dept": department},
    ).scalar_one()
    if current != name:
        raise ValueError("此統編已有不同名稱，請確認供應商名稱，不自動合併")


def save(
    db: Session, department: UUID, sample: UUID, payload: dict[str, Any], expected: int
) -> None:
    """新增或 CAS 更新整份修訂快照，拒絕重複／過期請求。"""
    params = {
        "id": sample,
        "dept": department,
        "data": json.dumps(payload, ensure_ascii=False),
        "version": expected,
    }
    if expected == 0:
        db.execute(
            text(
                "INSERT INTO invoiceguard_showcase.samples(id,department_id,version,payload) VALUES(:id,:dept,1,CAST(:data AS jsonb))"
            ),
            params,
        )
    else:
        count = db.execute(
            text("""
            UPDATE invoiceguard_showcase.samples SET payload=CAST(:data AS jsonb),
              version=version+1,updated_at=clock_timestamp()
            WHERE id=:id AND department_id=:dept AND version=:version
        """),
            params,
        ).rowcount
        if count != 1:
            raise ConflictError


def event(
    db: Session,
    department: UUID,
    sample: UUID,
    actor: UUID,
    action: str,
    reason: str,
    version: int,
    details: dict[str, Any],
) -> None:
    """與業務變更同交易追加事件，不提供修改／刪除操作。"""
    db.execute(
        text("""
        INSERT INTO invoiceguard_showcase.events(department_id,sample_id,actor_id,action,reason,version,details)
        VALUES(:dept,:sample,:actor,:action,:reason,:version,CAST(:details AS jsonb))
    """),
        {
            "dept": department,
            "sample": sample,
            "actor": actor,
            "action": action,
            "reason": reason,
            "version": version,
            "details": json.dumps(details, ensure_ascii=False),
        },
    )


def history(db: Session, department: UUID, sample: UUID, viewer: UUID) -> list[dict[str, Any]]:
    """共用範例異動紀錄，測試事件僅顯示給執行部門。"""
    return [
        dict(row)
        for row in db.execute(
            text("""
        SELECT actor_id,action,reason,version,details,occurred_at FROM invoiceguard_showcase.events
        WHERE sample_id=:id AND (
            (department_id=:dept AND action NOT IN ('test','ocr_test'))
            OR (department_id=:viewer AND action IN ('test','ocr_test'))
        ) ORDER BY id DESC LIMIT 200
    """),
            {"dept": department, "id": sample, "viewer": viewer},
        ).mappings()
    ]


def reserve(db: Session, department: UUID, actor: UUID, key: str, mime: str, digest: str) -> None:
    """先持久登錄來源再上傳；失敗或孤兒檔亦有可追溯紀錄，不刪原圖。"""
    db.execute(
        text("""
        INSERT INTO invoiceguard_showcase.uploads(object_key,department_id,actor_id,mime,digest)
        VALUES(:key,:dept,:actor,:mime,:digest)
    """),
        {"key": key, "dept": department, "actor": actor, "mime": mime, "digest": digest},
    )


def upload_state(db: Session, key: str, state: str) -> None:
    """標記來源已上傳／已關聯；不刪除來源。"""
    db.execute(
        text("UPDATE invoiceguard_showcase.uploads SET state=:state WHERE object_key=:key"),
        {"key": key, "state": state},
    )


def tests(db: Session, department: UUID, sample: UUID) -> list[dict[str, Any]]:
    """讀取固定範例最近二十筆測試，不跨部門。"""
    return [
        dict(row)
        for row in db.execute(
            text("""
        SELECT id,revision_id,note,created_at FROM invoiceguard_showcase.tests
        WHERE department_id=:dept AND sample_id=:sample ORDER BY created_at DESC LIMIT 20
    """),
            {"dept": department, "sample": sample},
        ).mappings()
    ]


def test_source(db: Session, department: UUID, sample: UUID, test: UUID) -> dict[str, Any]:
    """查閱測試原件引用，對象與所屬範例均須相符。"""
    row = db.execute(
        text("""
        SELECT source FROM invoiceguard_showcase.tests
        WHERE id=:test AND department_id=:dept AND sample_id=:sample
    """),
        {"test": test, "dept": department, "sample": sample},
    ).scalar_one_or_none()
    if row is None:
        raise MissingError
    return row


def save_test(
    db: Session,
    department: UUID,
    actor: UUID,
    sample: UUID,
    revision: UUID,
    source: dict[str, Any],
    note: str,
) -> UUID:
    """保存測試原件與固定參考版本，不寫正式發票。"""
    return db.execute(
        text("""
        INSERT INTO invoiceguard_showcase.tests(department_id,actor_id,sample_id,revision_id,source,note)
        VALUES(:dept,:actor,:sample,:revision,CAST(:source AS jsonb),:note) RETURNING id
    """),
        {
            "dept": department,
            "actor": actor,
            "sample": sample,
            "revision": revision,
            "source": json.dumps(source),
            "note": note,
        },
    ).scalar_one()


def duplicate_test_image(db: Session, department: UUID, test: UUID, digest: str) -> bool:
    """只檢查本部門其他測試原圖，不宣稱涵蓋正式發票。"""
    return bool(
        db.execute(
            text("""
        SELECT EXISTS(SELECT 1 FROM invoiceguard_showcase.tests
          WHERE department_id=:dept AND id<>:test AND source->>'sha256'=:digest)
    """),
            {"dept": department, "test": test, "digest": digest},
        ).scalar_one()
    )
