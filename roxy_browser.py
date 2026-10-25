"""RoxyChrome 引擎：本地指纹注入浏览器 + Playwright CDP 连接。

背景
----
xAI 注册页的资料页提交被 Turnstile 硬门禁拦着：

    ex = async e => {
        if (!B) return track("account_signup_error", {error_type: "turnstile_failed"}), eg(), !1;
        ...
        await l.signUp.createAccount({ ..., turnstileToken: B, ... });
    };

`B` 由 `<Turnstile onToken={K}/>` 通过 `turnstile.render()` 的 callback 注入。
在 Xvfb + SwiftShader 软件渲染的普通 Chromium 下，Cloudflare 会返回
error code `600010`（generic challenge failure / bot behavior detected），
widget 里只剩一个空的 `cf-turnstile-response`，token 永远为空，
于是提交函数在第一行就 return，一个网络请求都不发。

RoxyChrome 是 RoxyBrowser 自带的 Chromium 155 魔改内核，编译期挂载了
`fingerprint_inject_service`，读取 `<user-data-dir>/lumi.conf`（AES-256-GCM
加密的 JSON）注入完整指纹：UA / navigator / screen / WebGL / Canvas /
AudioContext / ClientRects / WebRTC / 时区 / 语言 / 代理。

注入后 WebGL 从 `SwiftShader Device (Subzero)` 变成真实 GPU 串，
Turnstile 立即放行，token 长度 770~800 字节。

lumi.conf 加密参数（逆自 `dist/main.mjs` 的 `createFingerprintConfigFile`）：

    createCipheriv("aes-256-gcm", "402ead7d23b43b6d1e0528d4f99c59bd",
                                     "3a105229aa31")
    base64(ciphertext || authTag)

密钥与 IV 都是 **ASCII 原始字节**（32 / 12 字节），不是 hex 解码。
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

# lumi.conf 加密参数（RoxyBrowser 4.0.8 硬编码，逆自 dist/main.mjs）
_LUMI_KEY = b"402ead7d23b43b6d1e0528d4f99c59bd"
_LUMI_IV = b"3a105229aa31"

DEFAULT_ROXY_CHROME = "/home/kali/.config/RoxyBrowser/chrome-bin/155/RoxyChrome"
DEFAULT_ROXY_STORE = "/home/kali/.config/RoxyBrowser/rox_unlock_store.json"

# 常见真实 GPU 组合，注入后 Turnstile 视为正常桌面环境
_GPU_PROFILES = [
    (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    (
        "Google Inc. (AMD)",
        "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    (
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
]

_UA_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36"
)


def _encrypt_lumi(payload: dict) -> str:
    """AES-256-GCM 加密并 base64，格式与 RoxyBrowser 完全一致。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(_LUMI_KEY).encrypt(_LUMI_IV, raw, None)  # 密文 || authTag
    return base64.b64encode(ct).decode("ascii")


def _noise(seed: Optional[str] = None) -> float:
    """生成稳定的 canvas/webgl 噪声值（0~1 之间的小数）。"""
    if seed:
        rnd = random.Random(seed)
    else:
        rnd = random.Random()
    return round(rnd.uniform(0.0001, 0.5), 6)


def build_lumi_config(
    *,
    user_agent: Optional[str] = None,
    chrome_version: str = "155",
    gpu: Optional[Tuple[str, str]] = None,
    proxy: Optional[dict] = None,
    locale: str = "en-US",
    timezone: str = "America/New_York",
    screen: Tuple[int, int] = (1920, 1080),
    seed: Optional[str] = None,
) -> dict:
    """构造 lumi.conf 的明文结构（字段名对齐 Chrome 分支的 $d() 返回值）。"""
    vendor, renderer = gpu or random.Random(seed).choice(_GPU_PROFILES)
    ua = user_agent or _UA_TEMPLATE.format(ver=chrome_version)
    w, h = screen

    cfg: dict = {
        "computerName": "DESKTOP-" + "".join(
            random.Random(seed or "").choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=7)
        ),
        "macAddress": ":".join(
            f"{random.Random((seed or '') + str(i)).randint(0, 255):02X}" for i in range(6)
        ),
        "searchEngine": {"name": "Google"},
        "appLocale": locale,
        "acceptLang": f"{locale},en;q=0.9",
        "timeZone": timezone,
        "chromeVersion": f"{chrome_version}.0.0.0",
        "chromeType": "Google Chrome",
        "userAgent": ua,
        "audioBuffer": {
            "version": "2",
            "enableAudioBufferNoise": True,
            "audioBufferNoiseValue": _noise((seed or "") + "audio"),
            "audioBufferNoiseInterval": 100,
        },
        "canvasContext": {
            "enableCanvasContextNoise": True,
            "canvasContextNoiseValue": _noise((seed or "") + "canvas"),
            "canvasContextNoiseValueV2": _noise((seed or "") + "canvas2"),
        },
        "clientRects": {
            "enable": True,
            "clientRectsNoiseFactorX": round(
                random.Random((seed or "") + "crx").uniform(0.3, 1.2), 6
            ),
            "clientRectsNoiseFactorY": round(
                random.Random((seed or "") + "cry").uniform(0.01, 0.09), 6
            ),
        },
        "WebGL": {
            "webglRenderer": renderer,
            "webglVendor": vendor,
            "enableWebGLRendererNoise": True,
            "webGLRendererNoiseInterval": 1,
            "webGLRendererNoiseValue": _noise((seed or "") + "webgl"),
        },
        "WebGPU": {"mode": "disable", "vendor": ""},
        "doNotTrack": False,
        "geoLocation": {
            "mode": "prompt",
            "enableFakeLocationData": True,
            "locationLatitude": 0,
            "locationLongitude": 0,
            "locationAccuracy": 0,
            "locationAltitude": 0,
        },
        "blockImages": False,
        "ignoreCertificateErrors": False,
        "disablePlayVideo": False,
        "disablePlaySound": False,
        "disablePasswordSaveTips": False,
        "navigator": {
            "platform": "Win32",
            "hardwareConcurrency": random.Random((seed or "") + "hc").choice([4, 8, 12, 16]),
            "maxTouchPoints": 0,
            "deviceMemory": random.Random((seed or "") + "dm").choice([8, 16]),
            "plugins": [
                {
                    "name": "PDF Viewer",
                    "path": "internal-pdf-viewer",
                    "desc": "Portable Document Format|Portable Document Format",
                    "version": "",
                    "mime": [
                        {"mime_type": "application/pdf", "file_extensions": "pdf"},
                        {"mime_type": "text/pdf", "file_extensions": "pdf"},
                    ],
                }
            ],
        },
        "userAgentMetadata": {
            "platform": "Windows",
            "mobile": False,
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "model": "",
        },
        "portScan": {"enablePortScanWhiteList": False, "portScanWhiteList": "45535"},
        "screen": {
            "pixelDepth": 24,
            "colorDepth": 24,
            "width": w,
            "height": h,
            "availWidth": w,
            "availHeight": h - 40,
            "devicePixelRatio": 1,
        },
        "speechSynthesis": {"enable": True, "voices": []},
        "webRtcMode": "disableOk",
        "ssl": {"cipherSuiteBlacklist": ""},
    }

    if proxy:
        host = str(proxy.get("host") or "").strip()
        port = int(proxy.get("port") or 0)
        ptype = str(proxy.get("type") or "http").lower()
        if host and port:
            cfg["fproxy"] = {
                "type": ptype,
                "host": host,
                "port": port,
                "username": str(proxy.get("username") or ""),
                "password": str(proxy.get("password") or ""),
                "proxyByPassList": "",
            }
    return cfg


def parse_proxy(proxy_str: str) -> Optional[dict]:
    """把 `http://user:pass@host:port` 或 `socks5://host:port` 转成 fproxy 字段。"""
    proxy_str = (proxy_str or "").strip()
    if not proxy_str:
        return None
    m = re.match(
        r"^(?P<scheme>[a-z0-9]+)://"
        r"(?:(?P<user>[^:@/]+):(?P<pw>[^@/]*)@)?"
        r"(?P<host>[^:/]+):(?P<port>\d+)",
        proxy_str,
        re.I,
    )
    if not m:
        return None
    scheme = m.group("scheme").lower()
    if scheme in ("socks5h", "socks5"):
        scheme = "socks5"
    return {
        "type": scheme,
        "host": m.group("host"),
        "port": int(m.group("port")),
        "username": m.group("user") or "",
        "password": m.group("pw") or "",
    }


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def resolve_chrome() -> str:
    """定位 RoxyChrome 可执行文件。"""
    env = os.environ.get("ROXY_CHROME_EXECUTABLE", "").strip()
    if env and Path(env).exists():
        return env
    if Path(DEFAULT_ROXY_CHROME).exists():
        return DEFAULT_ROXY_CHROME
    # 兜底：扫 chrome-bin 下所有版本
    base = Path.home() / ".config" / "RoxyBrowser" / "chrome-bin"
    if base.is_dir():
        for cand in sorted(base.glob("*/RoxyChrome"), reverse=True):
            if cand.exists():
                return str(cand)
    raise RuntimeError(
        "未找到 RoxyChrome，可用 ROXY_CHROME_EXECUTABLE 指定路径"
    )


def _wait_cdp(port: int, timeout: float = 30.0) -> dict:
    """等 CDP 端口就绪并返回 /json/version。"""
    import urllib.request

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4)
    raise RuntimeError(f"RoxyChrome CDP 未就绪(port={port}): {last}")


class RoxyChromeProcess:
    """启动 / 关闭一个带指纹注入的 RoxyChrome 实例。"""

    def __init__(
        self,
        *,
        profile_dir: Optional[str] = None,
        proxy_str: str = "",
        headless: bool = False,
        log_callback=None,
        seed: Optional[str] = None,
        chrome_version: str = "155",
        locale: str = "en-US",
        timezone: str = "America/New_York",
        window_size: Tuple[int, int] = (1920, 1080),
    ) -> None:
        self.log = log_callback or (lambda *_a, **_k: None)
        self.proxy_str = proxy_str
        self.headless = headless
        self.seed = seed or base64.urlsafe_b64encode(os.urandom(9)).decode()
        self.chrome_version = chrome_version
        self.locale = locale
        self.timezone = timezone
        self.window_size = window_size
        self._tmp_profile = False

        if profile_dir:
            self.profile_dir = Path(profile_dir)
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.profile_dir = Path(tempfile.mkdtemp(prefix="roxyprof-"))
            self._tmp_profile = True

        self.port = find_free_port()
        self.proc: Optional[subprocess.Popen] = None
        self.version: dict = {}

    def write_lumi(self) -> Path:
        """生成 lumi.conf 到 user-data-dir 根目录。"""
        proxy = parse_proxy(self.proxy_str)
        cfg = build_lumi_config(
            chrome_version=self.chrome_version,
            proxy=proxy,
            locale=self.locale,
            timezone=self.timezone,
            screen=self.window_size,
            seed=self.seed,
        )
        path = self.profile_dir / "lumi.conf"
        path.write_text(_encrypt_lumi(cfg), encoding="ascii")
        self.log(
            f"[*] lumi.conf 已生成 ({path.stat().st_size}B) "
            f"WebGL={cfg['WebGL']['webglRenderer'][:46]}"
        )
        if proxy:
            self.log(
                f"[*] 指纹内置代理 {proxy['type']}://{proxy['host']}:{proxy['port']}"
            )
        return path

    def start(self) -> dict:
        exe = resolve_chrome()
        self.write_lumi()
        args = [
            exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--password-store=basic",
            "--disable-backgrounding-occluded-windows",
            "--disable-blink-features=AutomationControlled",
            "--remote-allow-origins=*",
            f"--window-size={self.window_size[0]},{self.window_size[1]}",
        ]
        if self.headless:
            args.append("--headless=new")
        env = dict(os.environ)
        # 代理只走 lumi.conf 的 fproxy；环境变量会污染其他组件
        for key in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            env.pop(key, None)
        env.setdefault("DISPLAY", os.environ.get("JUNG_DISPLAY", ":99"))
        self.log(f"[*] 启动 RoxyChrome CDP={self.port} ({Path(exe).name})")
        self.proc = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.version = _wait_cdp(self.port)
        self.log(f"[*] RoxyChrome 就绪: {self.version.get('Browser', '?')}")
        return self.version

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self.proc = None
        if self._tmp_profile:
            shutil.rmtree(self.profile_dir, ignore_errors=True)


def connect_playwright(port: int, pw):
    """用 Playwright 接管已启动的 RoxyChrome（CDP），返回 (browser, context)。"""
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()
    return browser, context


def is_available() -> bool:
    try:
        resolve_chrome()
        return True
    except Exception:  # noqa: BLE001
        return False
