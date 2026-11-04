@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DEMO_PYTHON=%~dp0..\..\InvoiceGuard-AI-ADMIN\.venv\Scripts\python.exe"
if not exist "%DEMO_PYTHON%" (
  echo 找不到既有 ADMIN Python 環境，請參考 docs\SUPPLIER_DEMO.md。
  pause
  exit /b 1
)
"%DEMO_PYTHON%" -B scripts\run_supplier_demo.py
pause
endlocal
