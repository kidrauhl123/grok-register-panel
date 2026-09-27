#!/usr/bin/env python3
"""Manage persistent multi-port SSH tunnel from local host to Tencent Cloud residential proxy pool."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "log"
PID_FILE = LOG_DIR / "tencent_tunnel.pid"
META_FILE = LOG_DIR / "ss_pool_meta.json"
TUNNEL_LOG = LOG_DIR / "tencent_tunnel.log"

SSH_TARGET = "niannian-cn"
START_PORT = 21001
END_PORT = 21052


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_tunnel_pid() -> Optional[int]:
    if PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if is_pid_running(pid):
                return pid
        except Exception:
            pass
    # Fallback to pgrep
    try:
        res = subprocess.run(
            ["pgrep", "-f", f"ssh.*{START_PORT}.*{SSH_TARGET}"],
            capture_output=True,
            text=True,
        )
        for line in res.stdout.splitlines():
            p = int(line.strip())
            if p != os.getpid() and is_pid_running(p):
                return p
    except Exception:
        pass
    return None


def stop_tunnel() -> bool:
    pid = get_tunnel_pid()
    stopped = False
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            pass
    # Kill any lingering forwarders
    try:
        subprocess.run(
            ["pkill", "-9", "-f", f"ssh.*{START_PORT}.*{SSH_TARGET}"],
            capture_output=True,
        )
        subprocess.run(
            ["pkill", "-9", "-f", f"ssh.*18081.*{SSH_TARGET}"],
            capture_output=True,
        )
    except Exception:
        pass
    if PID_FILE.is_file():
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    return stopped


def start_tunnel() -> int:
    stop_tunnel()
    time.sleep(0.5)

    cmd = [
        "ssh",
        "-fN",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "TCPKeepAlive=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        # Compat port for single proxy
        "-L", f"127.0.0.1:18081:127.0.0.1:{START_PORT}",
    ]

    for port in range(START_PORT, END_PORT + 1):
        cmd.extend(["-L", f"127.0.0.1:{port}:127.0.0.1:{port}"])

    cmd.append(SSH_TARGET)

    print(f"[*] Starting SSH tunnel forwarding ports {START_PORT}-{END_PORT} to {SSH_TARGET}...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[-] Failed to start SSH tunnel: {res.stderr}")
        sys.exit(1)

    time.sleep(1.0)
    pid = get_tunnel_pid()
    if not pid:
        print("[-] Tunnel command succeeded but PID not found!")
        sys.exit(1)

    PID_FILE.write_text(str(pid), encoding="utf-8")
    print(f"[+] SSH tunnel active with PID {pid}")
    return pid


def probe_single_port(port: int, timeout: float = 6.0) -> dict:
    import requests
    proxy_url = f"socks5h://127.0.0.1:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    start = time.monotonic()
    
    # Try ipwho.is first, then ipinfo.io, then ipify
    endpoints = [
        ("https://ipwho.is/", lambda d: (d.get("ip"), d.get("connection", {}).get("asn"), d.get("connection", {}).get("org") or d.get("connection", {}).get("isp"))),
        ("https://ipinfo.io/json", lambda d: (d.get("ip"), int(d.get("org", "").split()[0].replace("AS", "")) if "AS" in d.get("org", "") else None, d.get("org"))),
        ("https://api.ipify.org?format=json", lambda d: (d.get("ip"), None, None)),
    ]

    for ep, extractor in endpoints:
        try:
            r = requests.get(ep, proxies=proxies, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                if ep.startswith("https://ipwho.is") and data.get("success") is False:
                    continue
                ip, asn, org = extractor(data)
                if ip:
                    latency = int((time.monotonic() - start) * 1000)
                    return {
                        "port": port,
                        "url": proxy_url,
                        "ok": True,
                        "ip": ip,
                        "asn": asn,
                        "org": org or "",
                        "latency_ms": latency,
                        "endpoint": ep,
                    }
        except Exception:
            continue

    return {
        "port": port,
        "url": proxy_url,
        "ok": False,
        "error": "Connection timed out or failed",
    }


def probe_all_ports(workers: int = 12) -> List[dict]:
    results = []
    ports = list(range(START_PORT, END_PORT + 1))
    print(f"[*] Probing {len(ports)} ports in parallel (workers={workers})...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(probe_single_port, p): p for p in ports}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            if res.get("ok"):
                print(f"  [+] Port {res['port']}: {res['ip']} ({res.get('org', '')[:25]}) - {res['latency_ms']}ms")
            else:
                print(f"  [-] Port {res['port']}: Failed")
    results.sort(key=lambda x: x["port"])
    return results


def sync_to_proxy_pool(probe_results: List[dict]):
    import hashlib
    state_file = LOG_DIR / "proxy_pool.json"
    
    meta_by_port = {}
    if META_FILE.is_file():
        try:
            meta_list = json.loads(META_FILE.read_text(encoding="utf-8"))
            for m in meta_list:
                meta_by_port[m["port"]] = m
        except Exception:
            pass

    existing_state = {"version": 1, "items": []}
    if state_file.is_file():
        try:
            existing_state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing_items = {item["url"]: item for item in existing_state.get("items", [])}
    now_str = _utc_now()
    new_items = []

    for res in probe_results:
        port = res["port"]
        url = f"socks5h://127.0.0.1:{port}"
        item_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        meta = meta_by_port.get(port, {})
        
        item = existing_items.get(url, {
            "id": item_id,
            "url": url,
            "enabled": True,
            "failure_count": 0,
            "success_count": 0,
            "risk_count": 0,
            "source": "tencent_tunnel",
            "created_at": now_str,
        })
        
        if res.get("ok"):
            item["status"] = "healthy"
            item["exit_ip"] = res["ip"]
            item["asn"] = res.get("asn")
            item["asn_org"] = res.get("org") or meta.get("tag", "")
            item["latency_ms"] = res.get("latency_ms")
            item["last_checked_at"] = now_str
            item["last_error"] = ""
            item["cooldown_until"] = ""
            item["cooldown_reason"] = ""
        else:
            item["status"] = "unhealthy"
            item["last_checked_at"] = now_str
            item["last_error"] = res.get("error", "probe failed")
            
        new_items.append(item)

    updated_state = {
        "version": 1,
        "items": new_items,
        "updated_at": now_str,
    }

    # Write atomically
    tmp_path = state_file.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(updated_state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(state_file)

    healthy_count = sum(1 for item in new_items if item["status"] == "healthy")
    print(f"[+] Successfully synced {len(new_items)} proxies ({healthy_count} healthy) to {state_file}")


def main():
    parser = argparse.ArgumentParser(description="Tencent Cloud multi-port tunnel manager")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "probe", "sync"])
    parser.add_argument("--workers", type=int, default=12, help="Parallel workers for probing")
    args = parser.parse_args()

    # Ensure meta file is copied if available
    tmp_meta = Path("/tmp/ss_pool_configs/meta.json")
    if tmp_meta.is_file() and not META_FILE.is_file():
        META_FILE.write_text(tmp_meta.read_text(encoding="utf-8"), encoding="utf-8")

    if args.action == "start":
        start_tunnel()
    elif args.action == "stop":
        if stop_tunnel():
            print("[+] Tunnel stopped.")
        else:
            print("[*] No active tunnel found.")
    elif args.action == "restart":
        start_tunnel()
    elif args.action == "status":
        pid = get_tunnel_pid()
        if pid:
            print(f"[+] Tunnel is running with PID {pid}")
        else:
            print("[-] Tunnel is not running")
    elif args.action == "probe":
        results = probe_all_ports(workers=args.workers)
        healthy = [r for r in results if r.get("ok")]
        print(f"\n[*] Probing completed: {len(healthy)} / {len(results)} healthy")
    elif args.action == "sync":
        pid = get_tunnel_pid()
        if not pid:
            print("[*] Tunnel not running, starting first...")
            start_tunnel()
        results = probe_all_ports(workers=args.workers)
        sync_to_proxy_pool(results)


if __name__ == "__main__":
    main()
