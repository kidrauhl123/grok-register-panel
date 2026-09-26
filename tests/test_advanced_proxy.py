# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import proxy_store
from advanced_proxy import expand_import_text, normalize_any_proxy
from grok2api_remote import _parse_import


class _Resp:
    def __init__(self, text):
        self.text = text


def test_normalize_vless_and_ss_uris():
    vless = (
        "vless://94c96fd8-b498-4a98-8b6e-4facdb63eb2e@198.41.223.41:2083"
        "?security=tls&type=ws&host=cfvpn.example.com&path=%2F#node"
    )
    out = proxy_store.normalize_proxy(vless)
    assert out.startswith("vless://")
    assert "198.41.223.41" in out
    ss = "ss://YWVzLTEyOC1nY206c2VjcmV0@ss.example.com:12022/?plugin=obfs-local;obfs=http#HK"
    parsed = normalize_any_proxy(ss)
    assert parsed.startswith("ss://")


def test_expand_subscription_base64():
    import base64

    body = "vless://11111111-1111-1111-1111-111111111111@1.1.1.1:443?type=ws&security=tls#a\n"
    blob = base64.b64encode(body.encode()).decode()
    lines = expand_import_text(blob)
    assert len(lines) == 1
    assert lines[0].startswith("vless://")


def test_parse_grok2api_import_complete():
    text = (
        "event: progress\n"
        "data: {\"created\":0}\n"
        "event: complete\n"
        "data: {\"created\":1,\"updated\":0,\"skipped\":0,\"synced\":1,\"syncFailed\":0}\n"
    )
    result = _parse_import(_Resp(text))
    assert result["created"] == 1
    assert result["synced"] == 1


if __name__ == "__main__":
    test_normalize_vless_and_ss_uris()
    test_expand_subscription_base64()
    test_parse_grok2api_import_complete()
    print("OK advanced proxy")
