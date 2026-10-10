"""AI 自動フォロー架電の法令対応（TDD）。

Phase 0 の調査で、AI さくらが掛ける自動フォロー架電に次の穴が見つかった:
  1. AI であることを告げていない（「さくらと申します」だけ）
  2. 勧誘に先立つ名乗り（事業者名・商品の種類・勧誘目的）を流していない
     （disclosure.py は手動発信の bridge_call にしか使われていなかった）
  3. 通話中に断られても、サーバー側で DNC に登録していない（LLM の指示だけ）

宅建業法施行規則16条の12（勧誘に先立つ明示・断った相手への勧誘の継続の禁止）を
満たすため、名乗りと拒否の処理は LLM の自由文に任せず、サーバー側で決定的に行う。
条文の適用は要専門家確認。番号はすべて架空。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import autofollow as af  # noqa: E402
from tac import disclosure  # noqa: E402
from tac import realtime as rt  # noqa: E402

NUM = "+819011110000"


def _entry(**over):
    e = {"number": NUM, "name": "架空", "category": "不在"}
    e.update(over)
    return e


@pytest.fixture
def disclosed(monkeypatch):
    monkeypatch.setattr(af.CONFIG, "company_name", "株式会社サンプル不動産")
    monkeypatch.setattr(af.CONFIG, "solicitation_product", "不動産売却の査定")
    monkeypatch.setattr(af.CONFIG, "public_base_url", "https://tac.example.test")


# ---- 1・2. 勧誘に先立つ名乗り（AI であること・事業者名・商品・勧誘目的） -------------------
def test_ai_disclosure_text_says_ai_company_product_and_purpose():
    t = disclosure.ai_text("株式会社サンプル不動産", "不動産売却の査定")
    assert "株式会社サンプル不動産" in t
    assert "AI" in t
    assert "不動産売却の査定" in t
    assert "勧誘" in t


def test_sakura_call_reads_the_disclosure_before_connecting_the_ai(disclosed):
    xml = af.twiml_connect_sakura(_entry(), "wss://voice.example/tac/media-stream")
    say = xml.index("<Say")
    connect = xml.index("<Connect>")
    assert say < connect, "名乗りは AI に接続する前に、サーバー側の固定文で流す"
    head = xml[:connect]
    assert "株式会社サンプル不動産" in head and "AI" in head and "勧誘" in head
    # 拒否されたとき DNC に登録できるよう、相手の番号を AI 側へ渡す
    assert f'<Parameter name="num" value="{NUM}" />' in xml


def test_dtmf_followup_also_reads_the_disclosure_first(disclosed):
    xml = af.twiml_followup_intro(_entry(), action_url="https://tac.example.test/tac/autofollow/dtmf")
    first_say = xml[xml.index("<Say"):]
    assert "株式会社サンプル不動産" in first_say.split("</Say>")[0]
    assert "勧誘" in first_say.split("</Say>")[0]


@pytest.mark.parametrize("missing", ["company_name", "solicitation_product"])
def test_placer_does_not_call_when_the_disclosure_cannot_be_read(monkeypatch, disclosed, missing):
    monkeypatch.setattr(af.CONFIG, missing, "")
    monkeypatch.setattr(af, "VOICE_STREAM_URL", "")
    import tac.outbound as outbound

    calls = []
    monkeypatch.setattr(outbound, "_create_call", lambda **kw: calls.append(kw) or {"ok": True})
    res = af.ivr_placer(_entry())
    assert calls == []
    assert res["ok"] is False and res.get("blocked") is True
    assert res.get("reason") == "disclosure_missing"


# ---- 3. AI 側が拒否を DNC に登録できないなら、AI に掛けさせない ------------------------
def test_placer_does_not_connect_the_ai_unless_the_voice_app_can_register_dnc(monkeypatch, disclosed):
    monkeypatch.setattr(af, "VOICE_STREAM_URL", "wss://voice.example/tac/media-stream")
    import tac.outbound as outbound

    calls = []
    monkeypatch.setattr(outbound, "_create_call", lambda **kw: calls.append(kw) or {"ok": True})
    monkeypatch.setattr(af, "_voice_dnc_ready", lambda url: False)
    res = af.ivr_placer(_entry())
    assert calls == [] and res["ok"] is False and res["reason"] == "voice_dnc_unavailable"

    monkeypatch.setattr(af, "_voice_dnc_ready", lambda url: True)
    af.ivr_placer(_entry())
    assert len(calls) == 1 and "<Connect>" in calls[0]["twiml"]


def test_voice_readiness_check_fails_closed(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert af._voice_dnc_ready("wss://voice.example/tac/media-stream") is False

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: R(json.dumps({"ok": True, "dnc_api_ready": False}).encode()))
    assert af._voice_dnc_ready("wss://voice.example/tac/media-stream") is False
    seen = {}

    def ok(req, timeout=0):
        seen["url"] = req.full_url if hasattr(req, "full_url") else req
        return R(json.dumps({"ok": True, "dnc_api_ready": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", ok)
    assert af._voice_dnc_ready("wss://voice.example/tac/media-stream") is True
    assert seen["url"] == "https://voice.example/"


# ---- 発話の判定（決定的なルール。LLM に任せない） ------------------------------------
@pytest.mark.parametrize("text", [
    "もう二度と電話しないでください",
    "いりません",
    "興味ないです",
    "結構です",
    "営業の電話は迷惑です",
    "必要ありません",
    "かけてこないで",
])
def test_refusals_are_detected(text):
    assert rt.classify_utterance(text) == "STOP"


@pytest.mark.parametrize("text", ["今ちょっと忙しいので", "運転中なんです", "また今度にしてください"])
def test_busy_is_detected(text):
    assert rt.classify_utterance(text) == "BUSY"


@pytest.mark.parametrize("text", ["はい、大丈夫です", "売却を考えています", "査定だけ聞きたいです", ""])
def test_ordinary_answers_are_not_refusals(text):
    assert rt.classify_utterance(text) is None


def _followup_state():
    s = rt.new_state()
    rt.on_twilio_event(
        {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1",
                                     "customParameters": {"mode": "followup", "num": NUM}}},
        s,
    )
    return s


def _heard(text):
    return {"type": "conversation.item.input_audio_transcription.completed", "transcript": text}


def test_refusal_in_a_followup_call_ends_the_call_and_requests_dnc_once():
    s = _followup_state()
    out = rt.on_openai_event(_heard("もう電話しないでください"), s)
    assert ("refused", {"number": NUM, "call_sid": "CA1"}) in out
    # 再生中の応答は止める（勧誘を続けない）
    assert ("openai", {"type": "response.cancel"}) in out
    assert rt.on_openai_event(_heard("いりません"), s) == []


def test_busy_in_a_followup_call_ends_politely_without_dnc():
    s = _followup_state()
    out = rt.on_openai_event(_heard("今忙しいので"), s)
    assert ("busy", {"call_sid": "CA1"}) in out
    assert not any(d == "refused" for d, _ in out)


def test_inbound_calls_are_not_treated_as_solicitation():
    s = rt.new_state()
    rt.on_twilio_event({"event": "start", "start": {"streamSid": "MZ2", "callSid": "CA2"}}, s)
    assert rt.on_openai_event(_heard("いりません"), s) == []


def test_closing_twiml_is_fixed_text_and_hangs_up():
    stop = rt.refusal_twiml()
    assert "今後" in stop and "お電話をしないよう登録" in stop and stop.rstrip().endswith("</Response>")
    assert "<Hangup/>" in stop
    busy = rt.busy_twiml()
    assert "<Hangup/>" in busy and "失礼いたします" in busy


# ---- DNC の登録（AI の音声アプリ → 本体の /tac/dnc） ----------------------------------
class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_register_dnc_posts_to_the_main_app_with_the_token(monkeypatch):
    sent = []

    def fake(req, timeout=0):
        sent.append(req)
        return _Resp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(rt.urllib.request, "urlopen", fake)
    monkeypatch.setattr(rt, "DNC_API_BASE", "https://tac.example.test")
    monkeypatch.setattr(rt, "DNC_API_TOKEN", "test-token-0000")
    assert rt.register_dnc_remote(NUM) is True
    req = sent[0]
    assert req.full_url == "https://tac.example.test/tac/dnc"
    assert req.get_header("X-tac-token") == "test-token-0000"
    assert b"action=add" in req.data and b"%2B819011110000" in req.data


def test_register_dnc_retries_and_reports_failure_without_raising(monkeypatch):
    attempts = []

    def boom(req, timeout=0):
        attempts.append(1)
        raise OSError("down")

    monkeypatch.setattr(rt.urllib.request, "urlopen", boom)
    monkeypatch.setattr(rt, "DNC_API_BASE", "https://tac.example.test")
    monkeypatch.setattr(rt, "DNC_API_TOKEN", "test-token-0000")
    monkeypatch.setattr(rt, "_sleep", lambda s: None)
    assert rt.register_dnc_remote(NUM) is False
    assert len(attempts) == 3


def test_register_dnc_is_not_ready_without_settings(monkeypatch):
    monkeypatch.setattr(rt, "DNC_API_BASE", "")
    monkeypatch.setattr(rt, "DNC_API_TOKEN", "")
    assert rt.dnc_api_ready() is False
    assert rt.register_dnc_remote(NUM) is False


# ---- 開口一番：名乗りは済んでいるので、会話を続けてよいかを確かめる -------------------------
def test_followup_greeting_does_not_pose_as_a_human_and_asks_for_permission():
    g = rt.build_greeting_response("followup", "架空")["response"]["instructions"]
    assert "さくらと申します" not in g
    assert "確認のお電話" not in g
    assert "よろしいでしょうか" in g
