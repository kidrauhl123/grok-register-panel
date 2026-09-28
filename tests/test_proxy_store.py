# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webui import proxy_store


class IsolatedStore:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.previous = (
            proxy_store.STATE_PATH,
            proxy_store.LOCK_PATH,
            proxy_store.LEGACY_PATH,
        )
        proxy_store.STATE_PATH = base / "log" / "proxy_pool.json"
        proxy_store.LOCK_PATH = base / "log" / "proxy_pool.json.lock"
        proxy_store.LEGACY_PATH = base / "proxies.txt"
        return base

    def __exit__(self, exc_type, exc, tb):
        proxy_store.STATE_PATH, proxy_store.LOCK_PATH, proxy_store.LEGACY_PATH = self.previous
        self.temp.cleanup()


def test_normalize_proxy_formats_and_rejects_paths():
    assert proxy_store.normalize_proxy("proxy.example:8080") == "http://proxy.example:8080"
    assert (
        proxy_store.normalize_proxy("proxy.example:8080:user:pass")
        == "http://user:pass@proxy.example:8080"
    )
    assert (
        proxy_store.normalize_proxy("HTTP://User:p%40ss@PROXY.EXAMPLE:8080/")
        == "http://User:p%40ss@proxy.example:8080"
    )
    try:
        proxy_store.normalize_proxy("http://proxy.example:8080/path")
    except proxy_store.ProxyValidationError:
        pass
    else:
        raise AssertionError("proxy paths must be rejected")
    vless = (
        "vless://94c96fd8-b498-4a98-8b6e-4facdb63eb2e@proxy.example:2083"
        "?security=tls&type=ws&path=%2F#n"
    )
    assert proxy_store.normalize_proxy(vless).startswith("vless://")
    assert proxy_store._probe_error_message(
        "ProxyError unable to connect to proxy http://user:secret@proxy.example:8080"
    ) == "无法连接代理"


def test_import_deduplicates_and_public_view_never_leaks_credentials():
    secret = "secret-password-77"
    with IsolatedStore():
        result = proxy_store.import_proxies(
            "\n".join(
                [
                    f"proxy.example:8080:worker:{secret}",
                    f"http://worker:{secret}@proxy.example:8080",
                    "broken-value",
                ]
            )
        )
        assert result["ok"] is True
        assert result["imported_count"] == 1
        assert result["duplicate_count"] == 0
        assert len(result["errors"]) == 1
        encoded = json.dumps(result, ensure_ascii=False)
        assert secret not in encoded
        assert "worker" not in result["items"][0]["display_url"]
        assert result["items"][0]["has_auth"] is True
        stored = proxy_store.STATE_PATH.read_text(encoding="utf-8")
        assert secret in stored
        assert stat.S_IMODE(proxy_store.STATE_PATH.stat().st_mode) == 0o600


def test_probe_result_and_runtime_cooldown_control_worker_selection():
    with IsolatedStore():
        imported = proxy_store.import_proxies("proxy.example:8080:user:pass")
        proxy_id = imported["imported_ids"][0]
        assert proxy_store.list_worker_proxies() == []
        assert proxy_store.worker_proxy_snapshot()["configured"] is True

        proxy_store._apply_probe_result(
            proxy_id,
            {
                "ok": True,
                "exit_ip": "203.0.113.9",
                "asn": 64500,
                "asn_org": "Example ISP",
                "latency_ms": 321,
                "checked_at": "2026-07-30T00:00:00Z",
            },
        )
        usable = proxy_store.list_worker_proxies()
        assert len(usable) == 1
        assert "user:pass" in usable[0]

        assert proxy_store.record_proxy_result(usable[0], "network", "connect timeout")
        assert proxy_store.list_worker_proxies() == []
        state = json.loads(proxy_store.STATE_PATH.read_text(encoding="utf-8"))
        state["items"][0]["cooldown_until"] = "2000-01-01T00:00:00Z"
        proxy_store.STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        usable_after = proxy_store.list_worker_proxies()
        assert usable_after == []
        assert proxy_store.read_proxy_pool()["items"][0]["stored_status"] == "unknown"

        proxy_store._apply_probe_result(
            proxy_id,
            {
                "ok": True,
                "exit_ip": "203.0.113.10",
                "asn": 64500,
                "asn_org": "Example ISP",
                "latency_ms": 222,
                "checked_at": "2026-07-30T00:05:00Z",
            },
        )
        usable = proxy_store.list_worker_proxies()
        assert proxy_store.record_proxy_result(usable[0], "risk", "policy deny")
        public = proxy_store.read_proxy_pool()
        item = public["items"][0]
        assert item["stored_status"] == "cooldown"
        assert item["cooldown_reason"] == "risk"
        assert item["risk_count"] == 1


def test_home_proxy_risk_stays_usable_and_cannot_disable():
    with IsolatedStore():
        imported = proxy_store.import_proxies("http://127.0.0.1:8003")
        proxy_id = imported["imported_ids"][0]
        proxy_store._apply_probe_result(
            proxy_id,
            {
                "ok": True,
                "exit_ip": "198.51.100.33",
                "asn": 64500,
                "asn_org": "Home",
                "latency_ms": 100,
                "checked_at": "2026-08-15T00:00:00Z",
            },
        )
        url = proxy_store.list_worker_proxies()[0]
        assert proxy_store.is_home_proxy(url)
        assert proxy_store.record_proxy_result(url, "risk", "botFlagSource=1")
        public = proxy_store.read_proxy_pool()["items"][0]
        assert public["stored_status"] != "cooldown"
        assert public["cooldown_reason"] == ""
        assert public["risk_count"] == 1
        assert url in proxy_store.list_worker_proxies()
        try:
            proxy_store.update_proxy(proxy_id, enabled=False)
        except proxy_store.ProxyValidationError as exc:
            assert "家宽" in str(exc)
        else:
            raise AssertionError("home proxies must stay enabled")
        restored = proxy_store.restore_home_proxies()
        assert restored["ok"] is True


def test_1024_ports_never_enter_worker_pool():
    with IsolatedStore():
        imported = proxy_store.import_proxies(
            "http://127.0.0.1:7902\nhttp://127.0.0.1:8003"
        )
        for item in imported["items"]:
            proxy_store._apply_probe_result(
                item["id"],
                {
                    "ok": True,
                    "exit_ip": "198.51.100.20",
                    "asn": 64500,
                    "asn_org": "Test",
                    "latency_ms": 50,
                    "checked_at": "2026-08-16T00:00:00Z",
                },
            )
        urls = proxy_store.list_worker_proxies()
        assert any(u.endswith(":8003") for u in urls)
        assert not any(":7902" in u for u in urls)


def test_xai_probe_uses_registration_page_result():
    calls = []

    class Response:
        status_code = 200
        text = "<html>Sign up</html>"
        headers = {}

    def successful_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    detail = proxy_store.probe_xai_signup(
        "http://proxy.example:8080",
        timeout=5,
        http_get=successful_get,
    )
    assert detail == "可达 HTTP 200"
    assert calls[0][1]["proxies"]["https"] == "http://proxy.example:8080"

    class ChallengeResponse:
        status_code = 403
        text = "Just a moment"
        headers = {"server": "cloudflare"}

    try:
        proxy_store.probe_xai_signup(
            "http://proxy.example:8080",
            timeout=5,
            http_get=lambda *_args, **_kwargs: ChallengeResponse(),
        )
    except RuntimeError as exc:
        assert "xAI 注册页不可用" in str(exc)
    else:
        raise AssertionError("Cloudflare challenge must fail the proxy probe")


def test_disable_delete_and_legacy_import():
    with IsolatedStore() as base:
        proxy_store.LEGACY_PATH.write_text(
            "http://a.example:8000\nhttp://b.example:8001\n", encoding="utf-8"
        )
        assert proxy_store.read_proxy_pool()["legacy"]["count"] == 2
        result = proxy_store.import_legacy_proxies()
        assert result["imported_count"] == 2
        proxy_id = result["items"][0]["id"]
        updated = proxy_store.update_proxy(proxy_id, enabled=False)
        assert next(item for item in updated["items"] if item["id"] == proxy_id)["enabled"] is False
        deleted = proxy_store.delete_proxy(proxy_id)
        assert deleted["deleted_id"] == proxy_id
        assert deleted["summary"]["total"] == 1


def test_async_probe_job_persists_health():
    with IsolatedStore():
        result = proxy_store.import_proxies("http://proxy.example:8080")
        proxy_id = result["imported_ids"][0]
        previous_probe = proxy_store.probe_proxy
        with proxy_store._TEST_LOCK:
            proxy_store._TEST_JOB.update(
                {
                    "running": False,
                    "job_id": None,
                    "testing_ids": [],
                }
            )
        proxy_store.probe_proxy = lambda url, timeout=8: {
            "ok": True,
            "exit_ip": "198.51.100.8",
            "asn": 64501,
            "asn_org": "Test Network",
            "latency_ms": 88,
            "checked_at": "2026-07-30T00:00:00Z",
        }
        try:
            job = proxy_store.start_proxy_tests([proxy_id])
            assert job["ok"] is True
            deadline = time.time() + 2
            while proxy_store.proxy_test_status()["running"] and time.time() < deadline:
                time.sleep(0.01)
            status = proxy_store.proxy_test_status()
            assert status["running"] is False
            assert status["healthy"] == 1
            item = proxy_store.read_proxy_pool()["items"][0]
            assert item["stored_status"] == "healthy"
            assert item["exit_ip"] == "198.51.100.8"
        finally:
            proxy_store.probe_proxy = previous_probe


def test_import_preserves_url_fragment_tag():
    with IsolatedStore():
        raw_ss = "ss://2022-blake3-aes-256-gcm:dGVzdA==@144.24.68.21:41808#%F0%9F%87%B0%F0%9F%87%B7%20%E9%9F%A9%E5%9B%BD%E5%AE%BD%E9%A2%91D1"
        imported = proxy_store.import_proxies(raw_ss)
        assert imported["ok"] is True
        pool = proxy_store.read_proxy_pool()
        assert len(pool["items"]) == 1
        item = pool["items"][0]
        assert item["tag"] == "🇰🇷 韩国宽频D1"


def test_compute_proxy_score_and_sorting():
    # 1. 评分计算测试
    # 健康王牌 (1.0x 倍率): 5杀 0亡 胜率100% 延迟150ms
    score_1x = proxy_store.compute_proxy_score(
        status="healthy",
        kills=5,
        deaths=0,
        total_battles=5,
        win_rate=100.0,
        multiplier=1.0,
        latency_ms=150,
        enabled=True,
    )
    # 20 (healthy) + 75 (kills) - 0 + 20 (wr) + 2.5 (battles) + 15.0 (1x mult) + 4.8 (lat) = 137.3
    assert score_1x == 137.3

    # 同等战绩下，10.0x 高倍率节点惩罚
    score_10x = proxy_store.compute_proxy_score(
        status="healthy",
        kills=5,
        deaths=0,
        total_battles=5,
        win_rate=100.0,
        multiplier=10.0,
        latency_ms=150,
        enabled=True,
    )
    # 10x 扣 16.5 分，相比 1x (+15分) 净差距 31.5 分
    assert score_10x == 105.8
    assert round(score_1x - score_10x, 1) == 31.5

    # 阵亡扣分严厉 (-35/次)
    score_with_death = proxy_store.compute_proxy_score(
        status="healthy",
        kills=5,
        deaths=1,
        total_battles=6,
        win_rate=83.3,
        multiplier=1.0,
        latency_ms=150,
        enabled=True,
    )
    assert score_with_death < score_1x - 30.0

    # 禁用节点惩罚
    score_disabled = proxy_store.compute_proxy_score(
        status="healthy",
        kills=5,
        deaths=0,
        total_battles=5,
        win_rate=100.0,
        multiplier=1.0,
        latency_ms=150,
        enabled=False,
    )
    assert score_disabled < -50.0

    # 异常节点
    score_unhealthy = proxy_store.compute_proxy_score(
        status="unhealthy",
        kills=0,
        deaths=0,
    )
    assert score_unhealthy == -45.0  # -60 (unhealthy) + 15 (1x mult)

    # 2. 排序测试
    items = [
        {"id": "1", "score": 50.0, "multiplier": 3.0, "kills": 2, "deaths": 1, "win_rate": 66.7, "total_battles": 3, "latency_ms": 200, "status": "healthy"},
        {"id": "2", "score": 120.0, "multiplier": 1.0, "kills": 6, "deaths": 0, "win_rate": 100.0, "total_battles": 6, "latency_ms": 100, "status": "healthy"},
        {"id": "3", "score": -30.0, "multiplier": 10.0, "kills": 1, "deaths": 3, "win_rate": 25.0, "total_battles": 4, "latency_ms": 500, "status": "unhealthy"},
    ]
    # 按综合评分排序
    by_score = proxy_store.sort_proxy_items(items, "score")
    assert [x["id"] for x in by_score] == ["2", "1", "3"]

    # 按倍率升序 (越低越好) 排序
    by_mult = proxy_store.sort_proxy_items(items, "multiplier")
    assert [x["id"] for x in by_mult] == ["2", "1", "3"]

    # read_proxy_pool(sort=...) 集成测试
    with IsolatedStore():
        proxy_store.import_proxies("proxy1.example:8081\nproxy2.example:8082")
        pool_sorted = proxy_store.read_proxy_pool(sort="score")
        assert pool_sorted["ok"] is True
        assert len(pool_sorted["items"]) == 2
        assert "score" in pool_sorted["items"][0]
        assert "multiplier" in pool_sorted["items"][0]


if __name__ == "__main__":
    test_normalize_proxy_formats_and_rejects_paths()
    test_import_deduplicates_and_public_view_never_leaks_credentials()
    test_probe_result_and_runtime_cooldown_control_worker_selection()
    test_xai_probe_uses_registration_page_result()
    test_disable_delete_and_legacy_import()
    test_async_probe_job_persists_health()
    test_home_proxy_risk_stays_usable_and_cannot_disable()
    test_1024_ports_never_enter_worker_pool()
    test_import_preserves_url_fragment_tag()
    test_compute_proxy_score_and_sorting()
    print("OK proxy store")

