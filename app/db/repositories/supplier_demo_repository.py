"""模組名稱：app.db.repositories.supplier_demo_repository。
功能說明：僅使用指定的本機 SQLite 檔；不匯入共用 DB、設定或 Storage。
注意事項：交易由 service 控制；唯一限制包含停用資料，搜尋採綁定參數。
"""

import json
import sqlite3
from pathlib import Path

# 對上層提供本機儲存錯誤介面；上層不依賴 SQLite 模組。
SupplierDataConflict = sqlite3.IntegrityError
SupplierStorageError = sqlite3.Error


class SupplierDemoRepository:
    """試作專用資料存取，不是正式 PostgreSQL Repository。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """開啟本機試作檔案。"""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self, db: sqlite3.Connection) -> None:
        """僅在本機試作檔案建立獨立表及唯一限制。"""
        db.execute("""CREATE TABLE IF NOT EXISTS suppliers (
            supplier_id TEXT PRIMARY KEY, supplier_name TEXT NOT NULL,
            country_code TEXT NOT NULL, tax_id TEXT NOT NULL, aliases TEXT NOT NULL,
            contact_name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT NOT NULL,
            address TEXT NOT NULL, payment_terms TEXT NOT NULL, payment_basis TEXT NOT NULL,
            payment_days INTEGER NOT NULL, payment_method TEXT NOT NULL, currency TEXT NOT NULL,
            notes TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS supplier_tax_unique ON suppliers(country_code,tax_id) WHERE tax_id <> ''"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(suppliers)")}
        if "credit_rating" not in columns:
            db.execute(
                "ALTER TABLE suppliers ADD COLUMN credit_rating TEXT NOT NULL DEFAULT 'unassessed'"
            )
        if "delivery_terms" not in columns:
            db.execute("ALTER TABLE suppliers ADD COLUMN delivery_terms TEXT NOT NULL DEFAULT ''")
        db.execute("""CREATE TABLE IF NOT EXISTS supplier_demo_function_settings (
            supplier_id TEXT PRIMARY KEY, selections TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def fetch_function_settings(self, db: sqlite3.Connection, supplier_id: str) -> dict | None:
        """讀取單一模擬供應商的功能列表設定。"""
        row = db.execute(
            "SELECT * FROM supplier_demo_function_settings WHERE supplier_id=?", (supplier_id,)
        ).fetchone()
        return dict(row) if row else None

    def save_function_settings(
        self, db: sqlite3.Connection, supplier_id: str, selections: frozenset[str], now: str
    ) -> None:
        """只更新獨立模擬設定，不修改供應商主檔或舊部門設定。"""
        db.execute(
            """INSERT INTO supplier_demo_function_settings (supplier_id,selections,updated_at)
            VALUES (?,?,?) ON CONFLICT(supplier_id) DO UPDATE SET
            selections=excluded.selections,updated_at=excluded.updated_at""",
            (supplier_id, json.dumps(sorted(selections)), now),
        )

    def lock_for_write(self, db: sqlite3.Connection) -> None:
        """依 service 的交易要求取得本機寫入鎖；提交仍由 service 負責。"""
        db.execute("BEGIN IMMEDIATE")

    def fetch(self, db: sqlite3.Connection, supplier_id: str) -> dict | None:
        """依識別碼取得一筆。"""
        row = db.execute("SELECT * FROM suppliers WHERE supplier_id = ?", (supplier_id,)).fetchone()
        return dict(row) if row else None

    def search(self, db: sqlite3.Connection, query: str) -> list[dict]:
        """字面包含搜尋，不把百分比或底線當萬用字元。"""
        rows = db.execute(
            """SELECT * FROM suppliers WHERE
            instr(lower(supplier_name),lower(?)) > 0 OR instr(lower(tax_id),lower(?)) > 0
            OR instr(lower(aliases),lower(?)) > 0 ORDER BY created_at DESC,supplier_id""",
            (query, query, query),
        )
        return [dict(row) for row in rows]

    def insert(self, db: sqlite3.Connection, record: dict) -> None:
        """寫入欄位固定的試作資料。"""
        db.execute(
            """INSERT INTO suppliers (
            supplier_id,supplier_name,country_code,tax_id,aliases,contact_name,phone,email,address,
            payment_terms,payment_basis,payment_days,payment_method,currency,notes,is_active,
            created_at,updated_at,credit_rating,delivery_terms) VALUES (
            :supplier_id,:supplier_name,:country_code,:tax_id,:aliases,:contact_name,:phone,
            :email,:address,:payment_terms,:payment_basis,:payment_days,:payment_method,
            :currency,:notes,:is_active,:created_at,:updated_at,:credit_rating,:delivery_terms)""",
            record,
        )

    def update(self, db: sqlite3.Connection, supplier_id: str, record: dict) -> None:
        """只修改可編輯欄位，保留識別碼、建立時間及啟用狀態。"""
        db.execute(
            """UPDATE suppliers SET supplier_name=:supplier_name,country_code=:country_code,
            tax_id=:tax_id,aliases=:aliases,contact_name=:contact_name,phone=:phone,email=:email,
            address=:address,payment_terms=:payment_terms,payment_basis=:payment_basis,
            payment_days=:payment_days,payment_method=:payment_method,currency=:currency,
            notes=:notes,updated_at=:updated_at,credit_rating=:credit_rating,
            delivery_terms=:delivery_terms WHERE supplier_id=:supplier_id""",
            {**record, "supplier_id": supplier_id},
        )

    def set_status(self, db: sqlite3.Connection, supplier_id: str, active: bool, now: str) -> None:
        """明確設定目標狀態，避免重送表單反覆切換。"""
        db.execute(
            "UPDATE suppliers SET is_active=?,updated_at=? WHERE supplier_id=?",
            (int(active), now, supplier_id),
        )
