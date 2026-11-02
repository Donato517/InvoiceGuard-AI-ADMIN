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

4. 設定環境變數後執行（資料庫名稱必須以 `ig_test` 開頭、主機必須是回送位址、不可有查詢參數）：

   ```bash
   export ISOLATED_CASE_DB_URL='postgresql+psycopg://<user>:<password>@127.0.0.1:55433/ig_test'
   export ISOLATED_CASE_DB_MARKER='<同一個隨機標記>'
   .venv/bin/python -B -m pytest -q tests/integration
   ```

   M1 測試另需同層 `InvoiceGuard-AI-API` 專案與它的 `.venv`（用來執行 API 真實的 `submit()`）。

5. 結束後移除容器：`docker rm -f ig-m1-test-pg`。

## 行為與限制

- 未設定 `ISOLATED_CASE_DB_URL`：整合測試略過。已設定但 URL 不安全、或資料庫缺少／不符標記：測試**失敗**，不寫入任何資料。
- 通過驗證後，只有那一個主機＋連接埠可被連線；其他資料庫、Storage、網路一律被封鎖
  （`tests/network_guard.py`）。播種子程序的環境是白名單，不繼承任何公司設定。
- 標記與回送位址都是輔助條件，不能防止有人用 SSH Tunnel／Port Forwarding 把別的資料庫轉到本機並複製標記。
  不要把正式資料庫轉到本機後再跑這些測試。
- `tests/integration/test_isolation_guard.py` 的負向測試全用替身，不需要資料庫。
