# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import grok2api_remote


class MockResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return json.loads(self.text)


def test_maybe_import_build_account_skips_when_disabled():
    with patch("grok2api_remote._config", return_value={"grok2api_auto_add_remote": False}):
        ok = grok2api_remote.maybe_import_build_account({"email": "test@example.com"})
        assert ok is False


def test_maybe_import_build_account_intercepts_unhealthy():
    cfg = {
        "grok2api_auto_add_remote": True,
        "grok2api_remote_base": "https://xai.premsir.com",
        "grok2api_remote_admin_username": "admin",
        "grok2api_remote_admin_password": "pass",
        "grok2api_remote_only_healthy": True,
        "grok2api_remote_target": "build",
    }
    messages = []
    with patch("grok2api_remote._config", return_value=cfg):
        ok = grok2api_remote.maybe_import_build_account(
            {"email": "test@example.com"},
            verdict="soft",
            log=messages.append,
        )
        assert ok is False
        assert any("已拦截" in m for m in messages)


def test_maybe_import_remote_routes_to_build():
    cfg = {
        "grok2api_auto_add_remote": True,
        "grok2api_remote_base": "https://xai.premsir.com",
        "grok2api_remote_admin_username": "admin",
        "grok2api_remote_admin_password": "pass",
        "grok2api_remote_only_healthy": True,
        "grok2api_remote_target": "build",
    }
    calls = []

    def mock_build(record, **kwargs):
        calls.append(("build", record))
        return True

    def mock_web(sso, **kwargs):
        calls.append(("web", sso))
        return True

    with patch("grok2api_remote._config", return_value=cfg), \
         patch("grok2api_remote.maybe_import_build_account", side_effect=mock_build), \
         patch("grok2api_remote.maybe_import_web_sso", side_effect=mock_web):
        ok = grok2api_remote.maybe_import_remote(
            record={"email": "build@example.com"},
            sso="sso=sample_cookie",
            email="build@example.com",
            verdict="healthy",
        )
        assert ok is True
        assert len(calls) == 1
        assert calls[0][0] == "build"
        assert calls[0][1]["email"] == "build@example.com"


def test_maybe_import_remote_routes_to_both():
    cfg = {
        "grok2api_auto_add_remote": True,
        "grok2api_remote_base": "https://xai.premsir.com",
        "grok2api_remote_admin_username": "admin",
        "grok2api_remote_admin_password": "pass",
        "grok2api_remote_only_healthy": True,
        "grok2api_remote_target": "both",
    }
    calls = []

    def mock_build(record, **kwargs):
        calls.append(("build", record))
        return True

    def mock_web(sso, **kwargs):
        calls.append(("web", sso))
        return True

    with patch("grok2api_remote._config", return_value=cfg), \
         patch("grok2api_remote.maybe_import_build_account", side_effect=mock_build), \
         patch("grok2api_remote.maybe_import_web_sso", side_effect=mock_web):
        ok = grok2api_remote.maybe_import_remote(
            record={"email": "both@example.com"},
            sso="sso=sample_cookie",
            email="both@example.com",
            verdict="healthy",
        )
        assert ok is True
        assert len(calls) == 2
        assert {c[0] for c in calls} == {"build", "web"}


if __name__ == "__main__":
    test_maybe_import_build_account_skips_when_disabled()
    test_maybe_import_build_account_intercepts_unhealthy()
    test_maybe_import_remote_routes_to_build()
    test_maybe_import_remote_routes_to_both()
    print("OK test_grok2api_remote")
