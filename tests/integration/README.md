# 整合測試（隔離 PostgreSQL）

這些測試會**寫入**資料庫，所以只允許連到你自己建立、並明確標記的本機測試庫。
CI 沒有設定下列變數，因此整合測試在 CI 會被略過；CI 綠燈不代表這些測試跑過。

## 步驟

1. 用本機容器建立全新資料庫（密碼自行產生，不要寫進檔案或文件；只綁回送位址）：

   ```bash
   docker run -d --name ig-m1-test-pg -e POSTGRES_PASSWORD="$PGPW" -e POSTGRES_DB=ig_test \
     -p 127.0.0.1:55433:5432 postgres:15
   ```

2. 套用 Schema v1.8（去掉 pgvector：移除 `CREATE EXTENSION vector` 與其檢查區塊，
   `extensions.vector(1536)` 改為 `text`；只在你的暫存副本修改，不改原檔）。

3. 在該資料庫寫入受控標記（至少 16 字元的隨機字串）：

   ```bash
   docker exec ig-m1-test-pg psql -U postgres -d ig_test \
     -c "ALTER DATABASE ig_test SET invoiceguard.m1_test_marker = '<隨機標記>'"
   ```

4. 設定環境變數後執行（主機必須是字面 `127.0.0.1` 或 `::1`，不接受 `localhost`；使用者必填、資料庫名稱必須以 `ig_test` 開頭、不可有查詢參數）：

   ```bash
   export ISOLATED_CASE_DB_URL='postgresql+psycopg://<user>:<password>@127.0.0.1:55433/ig_test'
   export ISOLATED_CASE_DB_MARKER='<同一個隨機標記>'
   .venv/bin/python -B -m pytest -q tests/integration
   ```

   M1 測試另需同層 `InvoiceGuard-AI-API` 專案與它的 `.venv`（用來執行 API 真實的 `submit()`）。

5. 結束後移除容器：`docker rm -f ig-m1-test-pg`。

## 行為與限制（請照實理解，這只是測試用的 Python 層防護）

- 未設定 `ISOLATED_CASE_DB_URL`：整合測試略過。已設定但 URL 不安全、或資料庫缺少／不符標記：測試**失敗**，不寫入任何資料。
- **Python 層連線封鎖**（`tests/network_guard.py`）只作用在「這個 Python 程序內」：攔截 Python socket、
  SQLAlchemy Engine 與 psycopg 的進入點。它不是 OS／容器層的網路隔離，也不是正式環境的安全機制。
- **資料庫身分比對：** SQLAlchemy／psycopg 連線的 host、port、dbname、user 四項必須與已驗證的身分完全相同才放行；
  不同 dbname 或 user、缺 dbname／user、`localhost`、Unix socket、多主機、`hostaddr`／`service` 一律拒絕。
  Python socket 一律拒絕（PostgreSQL 走 libpq，不需要它），所以 socket 層本身無法辨識 dbname／user，
  不能說「所有底層連線都受資料庫身分限制」。Windows 的程序內 `socketpair` 仍可使用。
- **播種子程序**（用 API 的 `.venv` 執行真實 `submit()`）是另一個程序，不受 pytest 補丁影響，所以 `m1_seed_via_api.py`
  在匯入任何 API 模組之前，自己重新驗證標記並安裝同一套 Python 層封鎖（只放行同一個四項身分）。
  它的環境變數是白名單（不含正式 `DATABASE_URL`、Storage、Token、API Key），資料庫 URL 與標記走環境變數、不在命令列。
- **標記驗證**只在連線驗證當下（測試程序一次、播種子程序一次）證明該資料庫是有人刻意標記的測試庫。
- **無法保證**防禦：SSH Tunnel／Port Forwarding 把別的資料庫轉到本機並複製標記、其他原生函式庫、
  測試中額外啟動的外部程式、以及在驗證通過後才被替換的資料庫。不要把正式資料庫轉到本機後再跑這些測試。
- `tests/integration/test_isolation_guard.py` 的負向測試全用替身，不需要資料庫；其中的子程序測試只會被拒絕連線，不會外連。
