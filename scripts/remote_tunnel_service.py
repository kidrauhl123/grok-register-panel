#!/usr/bin/env python3
"""Manage grok-register-panel WebUI monitor and tryCloudflare tunnel."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "log"
STATUS_FILE = LOG_DIR / "remote_panel.json"
MONITOR_LOG = LOG_DIR / "monitor_web.log"
TUNNEL_LOG = LOG_DIR / "cloudflared.log"

DEFAULT_PORT = 18787
DEFAULT_TOKEN = "grok2026-remote"


def _detect_display() -> str:
    env_display = os.environ.get("DISPLAY")
    if env_display:
        return env_display
    x11_dir = Path("/tmp/.X11-unix")
    if x11_dir.is_dir():
        for f in sorted(x11_dir.glob("X*"), key=lambda p: p.stat().st_mtime, reverse=True):
            num = f.name[1:]
            if num.isdigit():
                return f":{num}"
    return ":1"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_status() -> dict:
    if STATUS_FILE.is_file():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            m_pid = int(data.get("monitor_pid", 0))
            t_pid = int(data.get("tunnel_pid", 0))
            data["monitor_alive"] = is_pid_running(m_pid)
            data["tunnel_alive"] = is_pid_running(t_pid)
            return data
        except Exception:
            pass
    return {"running": False, "monitor_alive": False, "tunnel_alive": False}


def _kill_pid(pid: int, timeout: float = 3.0):
    if not is_pid_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not is_pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_service(port: int = DEFAULT_PORT):
    st = get_status()
    m_pid = st.get("monitor_pid")
    t_pid = st.get("tunnel_pid")
    stopped = []

    if t_pid and is_pid_running(t_pid):
        _kill_pid(t_pid)
        stopped.append(f"cloudflared (PID {t_pid})")

    if m_pid and is_pid_running(m_pid):
        _kill_pid(m_pid)
        stopped.append(f"monitor.py (PID {m_pid})")

    # Also clean up any orphan cloudflared tunnels pointing to our port
    try:
        res = subprocess.run(
            ["pgrep", "-f", f"cloudflared.*{port}"],
            capture_output=True,
            text=True,
        )
        for line in res.stdout.splitlines():
            pid = int(line.strip())
            if pid != os.getpid() and is_pid_running(pid):
                _kill_pid(pid)
                stopped.append(f"orphan cloudflared (PID {pid})")
    except Exception:
        pass

    # Clean up any orphan monitor.py processes
    try:
        res = subprocess.run(
            ["pgrep", "-f", "webui/monitor.py"],
            capture_output=True,
            text=True,
        )
        for line in res.stdout.splitlines():
            pid = int(line.strip())
            if pid != os.getpid() and is_pid_running(pid):
                _kill_pid(pid)
                stopped.append(f"orphan monitor.py (PID {pid})")
    except Exception:
        pass

    # Wait until port is actually free
    for _ in range(30):
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                break
        except OSError:
            time.sleep(0.2)

    if STATUS_FILE.is_file():
        try:
            STATUS_FILE.unlink()
        except OSError:
            pass

    print("[*] Remote panel service stopped: " + (", ".join(stopped) if stopped else "none running"))


def start_service(port: int = DEFAULT_PORT, token: str = DEFAULT_TOKEN, daemon: bool = False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    st = get_status()
    if st.get("monitor_alive") and st.get("tunnel_alive") and st.get("url"):
        print(f"[!] Remote panel already running at: {st.get('url')}")
        print(f"[*] Access Token: {st.get('token')}")
        return st

    stop_service(port=port)

    # 1. Start monitor.py
    display = _detect_display()
    env = os.environ.copy()
    env["MONITOR_HOST"] = "127.0.0.1"
    env["MONITOR_PORT"] = str(port)
    env["MONITOR_TOKEN"] = token
    env["CPA_AUTH_DIR"] = str(ROOT / "cpa_auth")
    env["DISPLAY"] = display
    env["GROK_HEADED"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PANEL_INCLUDE_TAIL"] = "1"

    m_log_f = open(MONITOR_LOG, "a", encoding="utf-8")
    m_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "webui" / "monitor.py")],
        cwd=str(ROOT),
        env=env,
        stdout=m_log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"[*] Started webui/monitor.py (PID {m_proc.pid}) on 127.0.0.1:{port}")

    # Wait for monitor.py to bind port
    bound = False
    for _ in range(30):
        time.sleep(0.2)
        if not is_pid_running(m_proc.pid):
            print("[-] monitor.py exited unexpectedly. Check log/monitor_web.log")
            return None
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                if resp.status == 200:
                    bound = True
                    break
        except Exception:
            pass

    if not bound:
        print("[-] monitor.py failed to bind port within 6s.")
        m_proc.terminate()
        return None

    # 2. Start cloudflared tunnel
    log_offset = TUNNEL_LOG.stat().st_size if TUNNEL_LOG.is_file() else 0
    t_log_f = open(TUNNEL_LOG, "a", encoding="utf-8")
    t_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        cwd=str(ROOT),
        stdout=t_log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"[*] Started cloudflared tunnel (PID {t_proc.pid})")

    # Read tunnel URL from log file (only newly appended content)
    tunnel_url = None
    start_t = time.time()
    while time.time() - start_t < 25:
        time.sleep(0.5)
        if not is_pid_running(t_proc.pid):
            print("[-] cloudflared exited unexpectedly. Check log/cloudflared.log")
            m_proc.terminate()
            return None
        if TUNNEL_LOG.is_file():
            try:
                with open(TUNNEL_LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(log_offset)
                    new_content = f.read()
                matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", new_content)
                if matches:
                    tunnel_url = matches[-1]
                    break
            except Exception:
                pass

    if not tunnel_url:
        print("[-] Timeout waiting for tryCloudflare URL. Check log/cloudflared.log")
        return None

    info = {
        "running": True,
        "url": tunnel_url,
        "local_url": f"http://127.0.0.1:{port}",
        "token": token,
        "monitor_pid": m_proc.pid,
        "tunnel_pid": t_proc.pid,
        "display": display,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    STATUS_FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("🚀 tryCloudflare 远程控制面板已就绪:")
    print(f"   公网访问地址: {tunnel_url}")
    print(f"   本地监听端口: 127.0.0.1:{port}")
    print(f"   面板认证Token: {token}")
    print(f"   X11 显示环境: DISPLAY={display}")
    print("=" * 64 + "\n")
    print("💡 远程控制提示:")
    print("   1. 浏览器打开上面的公网链接。")
    print("   2. 在网页顶部「面板 Token」输入框填入上述 Token。")
    print("   3. 即可在手机或任意外部网络随时查看注册进度、启动/停止批次、测试代理与账号！")

    return info


def print_status():
    st = get_status()
    if st.get("monitor_alive") and st.get("tunnel_alive") and st.get("url"):
        print("=" * 64)
        print("✅ 远程控制服务运行中:")
        print(f"   公网访问地址: {st.get('url')}")
        print(f"   本地地址:     {st.get('local_url')}")
        print(f"   面板 Token:   {st.get('token')}")
        print(f"   Monitor PID:  {st.get('monitor_pid')} (alive)")
        print(f"   Tunnel PID:   {st.get('tunnel_pid')} (alive)")
        print(f"   启动时间:     {st.get('started_at')}")
        print("=" * 64)
    else:
        print("[*] 远程控制服务未运行或已停止。运行 'python3 scripts/remote_tunnel_service.py start' 启动。")


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "start").lower()
    if cmd == "start":
        start_service()
    elif cmd == "stop":
        stop_service()
    elif cmd == "restart":
        stop_service()
        time.sleep(1)
        start_service()
    elif cmd == "status":
        print_status()
    else:
        print(f"Usage: {sys.argv[0]} [start|stop|restart|status]")


if __name__ == "__main__":
    main()
