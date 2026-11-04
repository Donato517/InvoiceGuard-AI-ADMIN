"""模組名稱：scripts.run_supplier_demo；功能說明：以乾淨環境啟動 loopback 模擬站。"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """不繼承 DB、Storage、模型及登入機密；只啟動本機試作入口。"""
    if any(os.environ.get(key) for key in ("VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME")):
        print("模擬模式只供本機使用，不可部署。")
        return 1
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "LOCALAPPDATA",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    print("供應商管理 模擬模式：http://127.0.0.1:8092/suppliers", flush=True)
    print("只使用本機虛構資料；關閉此視窗或按 Ctrl+C 即停止試作。", flush=True)
    try:
        return subprocess.call(
            [
                sys.executable,
                "-B",
                "-m",
                "uvicorn",
                "scripts.supplier_demo_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8092",
                "--no-proxy-headers",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
