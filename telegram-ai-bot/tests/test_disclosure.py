"""勧誘に先立つ名乗り（勧誘目的の明示）の自動アナウンスのテスト（TDD）。

電話で勧誘するときは、勧誘に先立って「事業者名・担当者名・商品の種類・勧誘目的」を
告げる（特商法§16 電話勧誘販売／宅建業法施行規則16条の12。適用は要専門家確認）。
担当者の言い忘れを仕組みで防ぎ、告げたことを架電記録に残す。既定 OFF（後方互換）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import calllog, disclosure, outbound  # noqa: E402
from tac.config import CONFIG  # noqa: E402

COMPANY = "株式会社Martial Arts"


# --- 純関数 ----------------------------------------------------------------------


def test_text_contains_company_agent_product_and_purpose():
    t = disclosure.text(COMPANY, "長谷川", "中古住宅")
    for part in (COMPANY, "長谷川", "中古住宅", "勧誘"):
        assert part in t
    assert t.index(COMPANY) < t.index("長谷川") < t.index("中古住宅")


def test_missing_lists_empty_items():
    assert disclosure.missing(COMPANY, "長谷川", "中古住宅") == []
    assert disclosure.missing("", " ", "中古住宅") == ["会社名", "担当者名"]
    assert disclosure.missing(COMPANY, "長谷川", "") == ["商品の種類"]


# --- TwiML -----------------------------------------------------------------------


@pytest.fixture
def cfg():
    """テストごとに関連設定を退避・復元する。"""
    keys = ("disclosure_enabled", "company_name", "agent_name", "solicitation_product",
            "record_calls", "agent_number", "calllog_file", "dnc_file", "daily_call_cap",
            "enforce_call_hours", "twilio_account_sid", "twilio_auth_token", "caller_id",
            "outbound_token")
    saved = {k: getattr(CONFIG, k) for k in keys}
    log = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    log.close()
    dnc = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    dnc.close()
    CONFIG.calllog_file, CONFIG.dnc_file = log.name, dnc.name
    CONFIG.daily_call_cap, CONFIG.enforce_call_hours = 0, False
    CONFIG.agent_number = "+818000000000"
    CONFIG.disclosure_enabled = False
    CONFIG.company_name, CONFIG.agent_name, CONFIG.solicitation_product = COMPANY, "長谷川", "中古住宅"
    yield CONFIG
    for k, v in saved.items():
        setattr(CONFIG, k, v)


def test_disabled_twiml_is_unchanged(cfg):
    before = outbound._conf_twiml("room-x", starter=False)
    assert outbound._conf_twiml("room-x", starter=False, disclosure_text="") == before
    assert "勧誘" not in before


def test_disclosure_goes_to_target_before_consent_notice(cfg):
    cfg.record_calls = True
    xml = outbound._conf_twiml("room-x", starter=False, disclosure_text="こちらはA社の山田です。")
    assert xml.index("こちらはA社の山田です。") < xml.index(cfg.recording_consent_text) < xml.index("<Dial>")


def test_disclosure_is_escaped(cfg):
    xml = outbound._conf_twiml("room-x", starter=False, disclosure_text="A&B <社>")
    assert "A&amp;B &lt;社&gt;" in xml and "<社>" not in xml


def test_agent_leg_never_hears_disclosure(cfg):
    xml = outbound._conf_twiml("room-x", starter=True, disclosure_text="こちらはA社です。")
    assert "こちらはA社です。" not in xml


# --- bridge_call -----------------------------------------------------------------


@pytest.fixture
def dialed(monkeypatch):
    """Twilio への発信を記録するだけの偽物に差し替える。"""
    calls: list[dict] = []

    def fake_create_call(*, to, twiml):
        calls.append({"to": to, "twiml": twiml})
        return {"ok": True, "sid": f"CA{len(calls)}", "to": to, "status": "queued"}

    monkeypatch.setattr(outbound, "_create_call", fake_create_call)
    return calls


def _log(cfg) -> list[dict]:
    return [json.loads(ln) for ln in Path(cfg.calllog_file).read_text(encoding="utf-8").splitlines() if ln]


def test_enabled_announces_on_target_leg_and_records_it(cfg, dialed):
    cfg.disclosure_enabled = True
    r = outbound.bridge_call("+81901112222")
    assert r["ok"] is True
    target, agent = dialed
    assert COMPANY in target["twiml"] and "長谷川" in target["twiml"] and "中古住宅" in target["twiml"]
    assert COMPANY not in agent["twiml"]
    [rec] = calllog._all()
    assert rec["status"] == "dialed" and rec["disclosed"] is True and rec["product"] == "中古住宅"


def test_agent_name_argument_overrides_default(cfg, dialed):
    cfg.disclosure_enabled = True
    outbound.bridge_call("+81901112222", agent_name="佐藤")
    assert "佐藤" in dialed[0]["twiml"] and "長谷川" not in dialed[0]["twiml"]


def test_enabled_but_missing_items_blocks_before_twilio(cfg, dialed):
    cfg.disclosure_enabled = True
    cfg.agent_name = ""
    r = outbound.bridge_call("+81901112222")
    assert r["ok"] is False and r["blocked"] is True
    assert "担当者名" in r["error"]
    assert dialed == []  # Twilio を呼んでいない
    [rec] = _log(cfg)
    assert rec["status"] == "blocked" and rec["reason"] == "disclosure_missing"


def test_disabled_keeps_previous_behaviour(cfg, dialed):
    cfg.company_name = ""  # OFF なら未設定でも従来どおり発信する
    r = outbound.bridge_call("+81901112222")
    assert r["ok"] is True
    assert "勧誘" not in dialed[0]["twiml"]
    [rec] = calllog._all()
    assert "disclosed" not in rec


def test_dnc_still_wins_over_disclosure(cfg, dialed):
    cfg.disclosure_enabled = True
    Path(cfg.dnc_file).write_text("+81901112222\n", encoding="utf-8")
    r = outbound.bridge_call("+81901112222")
    assert r["blocked"] is True and "DNC" in r["error"]
    assert dialed == []


# --- ルート（flask 未導入ならスキップ） ---------------------------------------------


def test_call_route_passes_agent_name(cfg, monkeypatch):
    import importlib

    try:
        server = importlib.import_module("tac.server")
    except Exception:  # noqa: BLE001 - flask 未導入環境ではスキップ
        return
    seen = {}

    def fake_bridge(to, *, agent=None, agent_name=None):
        seen.update(to=to, agent=agent, agent_name=agent_name)
        return {"ok": True}

    monkeypatch.setattr(outbound, "bridge_call", fake_bridge)
    monkeypatch.setattr(cfg, "agents", "")
    cfg.outbound_token = "t0ken"
    r = server.app.test_client().post(
        "/tac/call", data={"to": "+81901112222", "agent_name": " 佐藤 ", "token": "t0ken"})
    assert r.status_code == 200
    # 担当者未指定なら名簿（空なら TAC_AGENT_NUMBER）から自動で選ばれる
    assert seen == {"to": "+81901112222", "agent": cfg.agent_number, "agent_name": "佐藤"}
