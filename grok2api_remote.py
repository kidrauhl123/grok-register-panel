"""Push Grok Web SSO into a chenyme grok2api admin API (xai.premsir.com)."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_token = ""
_expires_at = None
_session_key = ""


class Grok2APIRemoteError(RuntimeError):
    pass


def _config():
    try:
        from grok_register_ttk import config
        if config:
            return dict(config)
    except Exception:
        pass
    try:
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _api_base(base: str) -> str:
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return ""
    for suffix in ("/api/admin/v1", "/admin/api", "/admin"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    parsed = urllib.parse.urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise Grok2APIRemoteError("chenyme grok2api 管理接口必须使用 HTTPS")
    return normalized + "/api/admin/v1"


def _post(url, *, headers=None, json_body=None, multipart=None, timeout=60):
    from curl_cffi import requests

    kwargs = {
        "headers": headers or {},
        "timeout": timeout,
        "proxies": {},
        "verify": True,
        "allow_redirects": True,
    }
    if multipart is not None:
        kwargs["multipart"] = multipart
    elif json_body is not None:
        kwargs["json"] = json_body
    return requests.post(url, **kwargs)


def _admin_token(api_base, username, password, force=False):
    global _token, _expires_at, _session_key
    session_key = "%s\n%s\n%s" % (
        api_base,
        username,
        hashlib.sha256(password.encode("utf-8")).hexdigest(),
    )
    with _lock:
        now = datetime.now(timezone.utc)
        if (
            not force
            and _session_key == session_key
            and _token
            and isinstance(_expires_at, datetime)
            and _expires_at > now + timedelta(seconds=30)
        ):
            return _token
        response = _post(
            api_base + "/auth/login",
            headers={"Content-Type": "application/json"},
            json_body={"username": username, "password": password},
            timeout=60,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            raise Grok2APIRemoteError("grok2api 管理员登录失败: HTTP %s" % status)
        tokens = (response.json() or {}).get("data", {}).get("tokens", {})
        token = str(tokens.get("accessToken") or "").strip()
        expiry = str(tokens.get("accessTokenExpiresAt") or "").strip()
        if not token:
            raise Grok2APIRemoteError("grok2api 登录响应缺少 accessToken")
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expires_at = now + timedelta(minutes=5)
        _token = token
        _expires_at = expires_at.astimezone(timezone.utc)
        _session_key = session_key
        return token


def _parse_import(response, secrets=()):
    current_event = ""
    completed = None
    for raw_line in str(getattr(response, "text", "") or "").splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except Exception:
            data = {"message": line[5:].strip()}
        if current_event == "error":
            message = str(data.get("message") or data.get("error") or "") if isinstance(data, dict) else ""
            for secret in secrets:
                if secret:
                    message = message.replace(str(secret), "[REDACTED]")
            raise Grok2APIRemoteError("grok2api 导入失败" + (": %s" % message[:200] if message else ""))
        if current_event == "complete":
            completed = data if isinstance(data, dict) else {}
    if completed is None:
        raise Grok2APIRemoteError("grok2api 导入响应缺少 complete 事件")
    return completed


def maybe_import_web_sso(sso: str, email: str = "", log=None) -> bool:
    global _token
    cfg = _config()
    if not bool(cfg.get("grok2api_auto_add_remote", False)):
        return False
    base = str(cfg.get("grok2api_remote_base") or "").strip()
    username = str(cfg.get("grok2api_remote_admin_username") or "").strip()
    password = str(cfg.get("grok2api_remote_admin_password") or "")
    logger = log or (lambda _message: None)
    token = str(sso or "").strip()
    if token.lower().startswith("sso="):
        token = token[4:]
    if not token or not base or not username or not password:
        if bool(cfg.get("grok2api_auto_add_remote", False)):
            logger("chenyme grok2api 远端导入未配置 base/用户名/密码，已跳过")
        return False
    api_base = _api_base(base)
    endpoint = api_base + "/accounts/web/import"
    from curl_cffi import CurlMime

    for attempt in range(2):
        access = _admin_token(api_base, username, password, force=attempt > 0)
        form = CurlMime()
        form.addpart(
            name="file",
            filename="grok-web-sso.txt",
            content_type="text/plain; charset=utf-8",
            data=(token + "\n").encode("utf-8"),
        )
        try:
            response = _post(
                endpoint,
                headers={"Authorization": "Bearer %s" % access, "Accept": "text/event-stream"},
                multipart=form,
                timeout=60,
            )
        finally:
            form.close()
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401 and attempt == 0:
            with _lock:
                global _token
                _token = ""
            continue
        if not 200 <= status < 300:
            raise Grok2APIRemoteError("grok2api SSO 导入失败: HTTP %s" % status)
        result = _parse_import(response, (token, access))
        summary = ", ".join(
            "%s=%s" % (key, result.get(key))
            for key in ("created", "updated", "skipped", "synced", "syncFailed")
            if result.get(key) is not None
        )
        if int(result.get("syncFailed") or 0):
            raise Grok2APIRemoteError("SSO 已导入，但初始同步失败" + (": %s" % summary if summary else ""))
        logger(
            "已导入 chenyme grok2api Grok Web"
            + (": %s" % summary if summary else "")
            + (" (%s)" % email if email else "")
        )
        return True
    raise Grok2APIRemoteError("grok2api 管理员认证已失效")
