"""Stop a running Agent3D instance started by launch.py."""
from __future__ import annotations

import os
import signal
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = os.getenv("AGENT3D_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT3D_PORT", "8060"))
PID_FILE = ROOT / "agent3d.pid"


def _pid_listening(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    # On Windows, tasklist is more reliable than os.kill for existence.
    import subprocess

    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in (r.stdout or "")


def main() -> int:
    pid = None
    if PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = None

    if pid and _pid_listening(pid):
        print(f"[Agent3D] 停止进程 PID={pid}")
        if sys.platform == "win32":
            import subprocess

            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
    else:
        # Best-effort: nothing tracked; try free the port only if our banner isn't needed
        print(f"[Agent3D] 未找到 PID 文件或进程已退出（端口 {HOST}:{PORT}）。")

    try:
        if PID_FILE.is_file():
            PID_FILE.unlink()
    except OSError:
        pass

    # Verify port freed
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            print(f"[Agent3D] 注意: 端口 {PORT} 仍被占用（可能是其他程序）。")
            return 1
    except OSError:
        print("[Agent3D] 已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
