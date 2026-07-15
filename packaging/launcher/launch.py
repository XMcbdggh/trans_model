"""Agent3D desktop launcher: start uvicorn, open browser, keep window open."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = os.getenv("AGENT3D_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT3D_PORT", "8060"))
URL = f"http://{HOST}:{PORT}/"
PID_FILE = ROOT / "agent3d.pid"


def _runtime_python() -> Path:
    bundled = ROOT / "runtime" / "python.exe"
    if bundled.is_file():
        return bundled
    return Path(sys.executable)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_ready(timeout_s: float = 60.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _http_ok(URL):
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.chdir(ROOT)
    py = _runtime_python()
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    # Prefer artifacts next to the install root
    env.setdefault("AGENT3D_SCENES", str(ROOT / "artifacts_web"))
    env.setdefault("AGENT3D_SETTINGS", str(ROOT / "agent3d_settings.json"))

    already = _port_open(HOST, PORT) and _http_ok(URL)
    proc: subprocess.Popen | None = None

    if already:
        print(f"[Agent3D] 服务已在运行: {URL}")
    else:
        if _port_open(HOST, PORT):
            print(f"[Agent3D] 端口 {PORT} 已被占用且无法访问本服务，请关闭占用进程或改端口。")
            print(f"         设置环境变量 AGENT3D_PORT 后重试。")
            input("按回车键退出…")
            return 1

        print(f"[Agent3D] 正在启动…")
        print(f"         Python: {py}")
        print(f"         地址:   {URL}")
        print(f"         目录:   {ROOT}")
        print()
        print("关闭本窗口会停止服务。首次使用请在网页右上角「模型设置」填写 API Key。")
        print("-" * 60)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        proc = subprocess.Popen(
            [
                str(py),
                "-m",
                "uvicorn",
                "agent3d.webapp.server:app",
                "--host",
                HOST,
                "--port",
                str(PORT),
            ],
            cwd=str(ROOT),
            env=env,
            creationflags=creationflags,
        )
        try:
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        except OSError:
            pass

        if not _wait_ready():
            print("[Agent3D] 启动超时，请查看上方报错。")
            if proc.poll() is None:
                proc.terminate()
            input("按回车键退出…")
            return 1

        print(f"[Agent3D] 已就绪: {URL}")

    try:
        webbrowser.open(URL)
    except Exception:
        print(f"[Agent3D] 请手动打开浏览器访问 {URL}")

    if proc is None:
        input("服务已在运行。按回车键退出本窗口（不会停止已有服务）…")
        return 0

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n[Agent3D] 正在停止…")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        try:
            if PID_FILE.is_file():
                PID_FILE.unlink()
        except OSError:
            pass

    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
