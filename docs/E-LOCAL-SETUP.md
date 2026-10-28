> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# 本機初始化設定與後台歡迎頁


> 2026-10-28 歡迎頁最新調整：僅顯示溫馨問候，移除設定流程及技術提示。功能入口保留在側欄，實際編輯頁仍顯示初始化提示。以下舊歡迎頁導覽描述為歷史紀錄。

> 2026-10-28 最新整合：部門內容與四項授權功能改為同頁儲存；獨立部門功能頁移除，舊 GET 僅轉址、舊 POST 停用。未接線功能可保存配置。詳見 [E-DEPARTMENT-DETAIL.md](E-DEPARTMENT-DETAIL.md)。下文舊矩陣 UI 流程僅供歷史追溯。


日期：2026-10-28。使用者為補齊功能與帳號部門設定，明確要求暫時恢復免登入；此規則取代 2026-10-27 四入口全面關閉的本機部分，舊紀錄保留供追溯。

## 操作入口

- 歡迎頁：http://invoiceguard-ai-admin.localhost:8080/welcome
- 功能列表：http://invoiceguard-ai-admin.localhost:8080/functions
- 新增功能：http://invoiceguard-ai-admin.localhost:8080/functions/new
- 部門權限：http://invoiceguard-ai-admin.localhost:8080/department-functions
- 部門：http://invoiceguard-ai-admin.localhost:8080/department
- 帳號：http://invoiceguard-ai-admin.localhost:8080/accounts

登入頁顯示本機設定入口；不需要先提交登入。歡迎頁只有固定導覽與說明，不查 DB、不虛構營運統計。原 /dashboard、/workspace、/system/* 與正式登入流程沒有免驗權例外。

## 範圍與恢復方式

`app/routers/e_admin.py` 的 LOCAL_SETUP_ENABLED=True 只適用上述配置路由。必須同時 APP_ENV=local、非 Vercel、解析後 backend=remote、peer 為 loopback 且 Host 在固定本機清單；不信任 forwarded 標頭。POST 仍經精確同源與原表單驗證。雲端配置入口維持 404。

此模式沒有登入操作者，暫不執行個人／主管資格與可授上限檢查；不能聲稱有完整 actor audit 或 G 管理隔離。沿用原服務的驗證、版本衝突、交易及停用行為，不建立虛假身分。頁面提供醒目提示。START 目前注入 Production DB，使用者儲存就是正式資料異動。

完成初始設定後將 LOCAL_SETUP_ENABLED 改為 False 並重啟 ADMIN，即恢復四入口身分、操作及 pending 整合閘門。若 B／G 尚未接線，會導向登入或回 503；不能假稱正式認證已完成。本機端口不能透過 tunnel／反向代理公開；loopback 不是公開入口的使用者身分驗證。

## 已有的部門與功能關聯

v1.3 schema 已有 public.departments、public.app_functions 及 public.department_permissions。對照表的 (department_id, function_id) 是複合主鍵，can_view/can_create/can_edit/can_delete 分開保存。部門功能頁沿用既有 repository 維護此表，並有變更預覽、版本衝突與確認儲存，不新增 DB 或 schema。

帳號與部門由 public.user_departments 關聯；功能目錄不會自動建立路由或授予權限。主管委派、可授上限與操作資料範圍無法只靠此對照表推導，仍待核定與正式接線。此次沒有連雲端 DB、沒有讀取實際帳號或修改正式資料。

## 驗證

新增 test_e_local_setup：真實路由與本機來源閘門、合成資料替身；測試免 Cookie 頁面、歡迎頁無 DB、部署／Host／peer／backend 拒絕、同源寫入及關閉模式後恢復拒絕。原 test_e_login_access 明確在 LOCAL_SETUP_ENABLED=False 下保留正式閘門測試，沒有把 pending 或身分 guard 覆寫成成功。

實際驗證結果於完成後補入開發紀錄。無真實 DB／正式登入驗收；未 Commit／Push／部署。
驗證完成（2026-10-28）：完整離線回歸 1107 passed、9 skipped、1 個既有 TestClient/httpx warning（124.13s）；新增 42 項本機模式案例。三站 checker PASS（0 issues）、Ruff lint 及 122 檔格式檢查通過，36 個樣板 lint 0 errors。本機 /welcome 實測 HTTP 200、設定模式提示及固定連結正常，no-store；此頁不查 DB。正式 DB 的功能列表／儲存留由使用者操作，未由代理讀寫。初輪新增測試誤用布林 VERCEL，已改為設定模型要求的字串；補齊歡迎頁新增功能捷徑後完整回歸通過。未 Commit／Push。
