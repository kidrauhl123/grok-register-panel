# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sso_to_auth_json import (
    _parse_grok_offer_state,
    classify_sso_offer,
    parse_sso_line,
    run_check_sso_offer,
)


TOKEN = "e" * 80


def test_classify_offer_matches_verdicts():
    assert (
        classify_sso_offer({
            "status_code": 200, "found": True, "free_trial": True, "free_trial_days": 3,
        })
        == "trial"
    )
    assert (
        classify_sso_offer({
            "status_code": 200, "found": True, "free_trial": False, "pay_upfront": True,
        })
        == "offer"
    )
    assert (
        classify_sso_offer({
            "status_code": 200, "found": True, "free_trial": False, "pay_upfront": False,
            "discount": True,
        })
        == "offer"
    )
    assert (
        classify_sso_offer({
            "status_code": 200, "found": True, "free_trial": False, "pay_upfront": False,
            "discount": False, "braintree": False,
        })
        == "none"
    )
    assert classify_sso_offer({"status_code": 401, "found": False, "error": "bad"}) == "error"
    assert classify_sso_offer({"status_code": 200, "found": False, "error": ""}) == "unknown"


def test_parse_offer_state_extracts_trial():
    payload = {
        "stripe": {
            "products": [
                {
                    "id": "prod_x",
                    "prices": [
                        {
                            "id": "price_1",
                            "campaign": {
                                "campaignId": "cmp_abc",
                                "stripe": {"freeTrial": {"freeTrialDays": 3}},
                            },
                        }
                    ],
                }
            ]
        }
    }
    state = _parse_grok_offer_state(payload)
    assert state["found"] is True
    assert state["has_campaign"] is True
    assert state["free_trial"] is True
    assert state["free_trial_days"] == 3
    assert state["offer_type"] == "free_trial"
    assert state["campaign_id"] == "cmp_abc"


def test_parse_offer_state_no_campaign():
    payload = {"stripe": {"products": [{"id": "prod_x", "prices": [{"id": "price_1"}]}]}}
    state = _parse_grok_offer_state(payload)
    assert state["found"] is True
    assert state["has_campaign"] is False
    assert state["free_trial"] is False
    assert state["offer_type"] == ""


def test_run_check_offer_classifies_and_exports():
    records = [
        parse_sso_line(f"trial@example.test----{TOKEN}"),
        parse_sso_line(f"none@example.test----{'f' * 80}"),
    ]
    states = {
        TOKEN: {
            "found": True, "has_campaign": True, "free_trial": True, "free_trial_days": 3,
            "pay_upfront": False, "discount": False, "braintree": False,
            "campaign_id": "cmp_1", "offer_type": "free_trial", "status_code": 200, "error": "",
        },
        "f" * 80: {
            "found": True, "has_campaign": False, "free_trial": False, "free_trial_days": 0,
            "pay_upfront": False, "discount": False, "braintree": False,
            "campaign_id": "", "offer_type": "", "status_code": 200, "error": "",
        },
    }

    def fake_inspect(sso, proxy="", log=print, timeout=20):
        return dict(states[sso])

    import sso_to_auth_json as mod

    previous = mod.inspect_sso_offer
    mod.inspect_sso_offer = fake_inspect
    try:
        with tempfile.TemporaryDirectory() as temp:
            export = Path(temp) / "offer.jsonl"
            summary = run_check_sso_offer(
                records,
                export=export,
                log=lambda *_args, **_kwargs: None,
            )
            assert summary["total"] == 2
            assert summary["trial_count"] == 1
            assert summary["none_count"] == 1
            text = export.read_text(encoding="utf-8")
            assert text.count("\n") == 2
            assert TOKEN not in text
            assert "trial@example.test" in text
    finally:
        mod.inspect_sso_offer = previous


if __name__ == "__main__":
    test_classify_offer_matches_verdicts()
    test_parse_offer_state_extracts_trial()
    test_parse_offer_state_no_campaign()
    test_run_check_offer_classifies_and_exports()
    print("OK offer")
