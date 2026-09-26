"""Resolve VLESS/SS/Trojan/etc. into a local HTTP endpoint for Camoufox."""
from __future__ import annotations

import threading

from proxy_protocol_runtime import ProtocolRuntimeManager
from proxy_protocols import (
    ADVANCED_SCHEMES,
    NATIVE_SCHEMES,
    ProxyProtocolError,
    parse_proxy_line,
    parse_subscription_source,
)

_TLS = threading.local()
_MANAGER_LOCK = threading.Lock()
_MANAGER = None


def runtime_manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ProtocolRuntimeManager()
        return _MANAGER


def looks_like_subscription(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or "://" in raw[:120]:
        return False
    compact = "".join(raw.split())
    return len(compact) >= 80 and all(
        ch.isalnum() or ch in "+/=" for ch in compact[:80]
    )


def expand_import_text(text: str) -> list[str]:
    raw = str(text or "")
    if looks_like_subscription(raw):
        result = parse_subscription_source(raw)
        return [node.raw_uri for node in result.nodes]
    lines = []
    for line in raw.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if looks_like_subscription(item):
            result = parse_subscription_source(item)
            lines.extend(node.raw_uri for node in result.nodes)
        else:
            lines.append(item)
    return lines


def normalize_any_proxy(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ProxyProtocolError("代理地址为空")
    scheme = value.split("://", 1)[0].lower() if "://" in value else "http"
    if scheme in ADVANCED_SCHEMES or scheme in NATIVE_SCHEMES or scheme == "socks":
        descriptor = parse_proxy_line(value if "://" in value else ("http://" + value))
        return descriptor.raw_uri
    raise ProxyProtocolError("不支持的代理协议: %s" % scheme)


def bind_thread_proxy(raw: str) -> str:
    unbind_thread_proxy()
    descriptor = parse_proxy_line(raw)
    endpoint, runtime_key = runtime_manager().acquire(descriptor)
    _TLS.runtime_key = runtime_key
    _TLS.endpoint = endpoint
    _TLS.source = descriptor.raw_uri
    return endpoint


def unbind_thread_proxy() -> None:
    key = getattr(_TLS, "runtime_key", None)
    if key:
        try:
            runtime_manager().release(key)
        except Exception:
            pass
    _TLS.runtime_key = None
    _TLS.endpoint = None
    _TLS.source = None


def resolve_temporary(raw: str):
    descriptor = parse_proxy_line(raw)
    endpoint, runtime_key = runtime_manager().acquire(descriptor)

    def release():
        runtime_manager().release(runtime_key)

    return endpoint, release
