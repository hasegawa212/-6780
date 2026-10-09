"""リアルタイム音声オペレーター「さくら」の会話品質テスト（TDD）。

Twilio Media Streams ↔ OpenAI Realtime のブリッジを、純粋関数に切り出して検証する。
電話で「いちばん会話が成立する」ために外せない仕様を固定する:

  - 低遅延: g711_ulaw を無変換で中継
  - 自然なターンテイキング: server_VAD を電話向けにチューニング
  - 聞き取り精度: 日本語を明示した文字起こしを有効化
  - 割り込み(barge-in): 相手が話し出したら再生を即停止し、
    モデルに「どこまで聞こえたか」を truncate で正確に伝える
  - 人格: 「恐れ入ります」だけで終わらせず会話を前に進める
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import realtime as rt  # noqa: E402


# --- TwiML -------------------------------------------------------------
def test_twiml_connects_media_stream_over_wss():
    xml = rt.build_twiml("voice.example.com")
    assert "<Connect>" in xml
    assert '<Stream url="wss://voice.example.com/tac/media-stream"' in xml
    assert xml.strip().startswith("<?xml")


# --- セッション設定（会話品質の心臓部） --------------------------------
def test_session_config_uses_g711_ulaw_both_ways():
    s = rt.build_session_config("inst", "marin")["session"]
    assert s["input_audio_format"] == "g711_ulaw"
    assert s["output_audio_format"] == "g711_ulaw"


def test_session_config_tunes_vad_for_phone_turn_taking():
    s = rt.build_session_config("inst", "marin")["session"]
    td = s["turn_detection"]
    assert td["type"] == "server_vad"
    # 相手が話し終わるまで待てるよう無音判定は長め（短すぎると途中で切る）
    assert td["silence_duration_ms"] >= 650
    # 小さめの声も拾えるよう閾値は控えめ
    assert td["threshold"] <= 0.5
    # 相手の第一声に前後の間を少し含める
    assert td["prefix_padding_ms"] >= 200
    # 無音を検知したら自動で応答を作る＝往復になる
    assert td["create_response"] is True
    # こちらが喋っている最中でも相手の発話で自動中断する
    assert td["interrupt_response"] is True


def test_session_config_enables_japanese_transcription():
    s = rt.build_session_config("inst", "marin")["session"]
    tr = s["input_audio_transcription"]
    assert tr["model"]  # 文字起こしモデル指定あり
    assert tr.get("language") == "ja"  # 日本語を明示して聞き取り精度を上げる


def test_session_config_carries_instructions_and_voice():
    s = rt.build_session_config("HELLO-INST", "cedar")["session"]
    assert s["instructions"] == "HELLO-INST"
    assert s["voice"] == "cedar"
    assert "audio" in s["modalities"] and "text" in s["modalities"]


# --- 人格（会話を前に進める） ------------------------------------------
def test_instructions_push_conversation_forward():
    inst = rt.build_instructions()
    assert "さくら" in inst
    assert "恐れ入ります" in inst  # 「恐れ入りますで終わらせない」という指示が含まれる
    # 聞き取れない時は黙らず、短く1つだけ聞き返す方針
    assert ("聞き返" in inst) or ("もう一度" in inst)


def test_greeting_response_is_a_response_create_with_question():
    g = rt.build_greeting_response()
    assert g["type"] == "response.create"
    txt = g["response"]["instructions"]
    assert "さくら" in txt
    assert "？" in txt


def test_followup_greeting_mentions_customer_and_is_outbound():
    g = rt.build_greeting_response(mode="followup", name="山田")
    txt = g["response"]["instructions"]
    assert "山田さま" in txt
    assert "お電話ありがとうございます" not in txt  # 着信用の挨拶ではない
    # 勧誘を「確認のお電話」と言い換えない。名乗りは接続前の固定文で済んでいるので、続けてよいかを確かめる
    assert "確認のお電話" not in txt
    assert "よろしいでしょうか" in txt


def test_session_config_exposes_transfer_tool():
    s = rt.build_session_config("inst", "marin")["session"]
    names = [t.get("name") for t in s.get("tools", [])]
    assert "transfer_to_agent" in names
    assert s.get("tool_choice") == "auto"


def test_start_captures_call_sid():
    state = rt.new_state()
    rt.on_twilio_event({"event": "start",
                        "start": {"streamSid": "ST1", "callSid": "CA777"}}, state)
    assert state["call_sid"] == "CA777"


def test_function_call_emits_transfer_action():
    state = rt.new_state()
    state["stream_sid"] = "ST1"
    state["call_sid"] = "CA777"
    cmds = rt.on_openai_event(
        {"type": "response.function_call_arguments.done",
         "name": "transfer_to_agent", "arguments": "{}"}, state)
    transfers = [p for d, p in cmds if d == "transfer"]
    assert transfers and transfers[0]["call_sid"] == "CA777"


def test_transfer_twiml_dials_agent_with_timeout_and_action():
    xml = rt.transfer_twiml("+819012345678", "https://voice.example/tac/handoff-result")
    assert "+819012345678</Dial>" in xml
    assert 'timeout="22"' in xml
    assert 'action="https://voice.example/tac/handoff-result"' in xml
    assert "担当者におつなぎします" in xml


def test_handoff_result_hangup_on_success():
    xml = rt.handoff_result_twiml("completed")
    assert "<Hangup/>" in xml
    assert "席を外して" not in xml


def test_handoff_result_apologizes_on_no_answer():
    for st in ("no-answer", "busy", "failed", ""):
        xml = rt.handoff_result_twiml(st)
        assert "改めて担当よりご連絡" in xml
        assert "<Hangup/>" in xml


def test_start_with_followup_params_emits_outbound_greeting():
    state = rt.new_state()
    cmds, _ = rt.on_twilio_event({
        "event": "start",
        "start": {"streamSid": "ST9", "customParameters": {"mode": "followup", "customer_name": "田中"}},
    }, state)
    assert state["mode"] == "followup"
    assert state["customer_name"] == "田中"
    greetings = [p for d, p in cmds if d == "openai" and p.get("type") == "response.create"]
    assert greetings and "田中さま" in greetings[0]["response"]["instructions"]
    # 二重挨拶しない
    cmds2, _ = rt.on_twilio_event({"event": "start", "start": {"streamSid": "ST9"}}, state)
    assert not [p for d, p in cmds2 if p.get("type") == "response.create"]


# --- Twilio → OpenAI ---------------------------------------------------
def test_twilio_start_sets_stream_sid():
    state = rt.new_state()
    cmds, stop = rt.on_twilio_event({"event": "start", "start": {"streamSid": "ST123"}}, state)
    assert state["stream_sid"] == "ST123"
    assert stop is False


def test_twilio_media_forwards_audio_and_tracks_timestamp():
    state = rt.new_state()
    rt.on_twilio_event({"event": "start", "start": {"streamSid": "ST1"}}, state)
    cmds, stop = rt.on_twilio_event(
        {"event": "media", "media": {"payload": "BASE64AUDIO", "timestamp": "120"}}, state
    )
    assert ("openai", {"type": "input_audio_buffer.append", "audio": "BASE64AUDIO"}) in cmds
    assert state["latest_media_ts"] == 120
    assert stop is False


def test_twilio_stop_signals_end():
    state = rt.new_state()
    _, stop = rt.on_twilio_event({"event": "stop"}, state)
    assert stop is True


# --- OpenAI → Twilio ---------------------------------------------------
def test_openai_audio_delta_streams_to_twilio_and_marks_assistant():
    state = rt.new_state()
    state["stream_sid"] = "ST1"
    state["latest_media_ts"] = 500
    cmds = rt.on_openai_event(
        {"type": "response.audio.delta", "delta": "CHUNK", "item_id": "item_1"}, state
    )
    sends = [p for d, p in cmds if d == "twilio" and p.get("event") == "media"]
    assert sends and sends[0]["media"]["payload"] == "CHUNK"
    assert sends[0]["streamSid"] == "ST1"
    # 応答の再生開始時刻と、どのアイテムを喋っているかを記録（barge-in 用）
    assert state["last_assistant_item"] == "item_1"
    assert state["response_start_ts"] == 500


def test_barge_in_truncates_and_clears_when_assistant_speaking():
    state = rt.new_state()
    state["stream_sid"] = "ST1"
    # さくらが item_1 を喋り始めた（再生開始 1000ms 時点）
    state["latest_media_ts"] = 1000
    rt.on_openai_event(
        {"type": "response.audio.delta", "delta": "C", "item_id": "item_1"}, state
    )
    # 400ms 後に相手が割り込んで喋り出した
    state["latest_media_ts"] = 1400
    cmds = rt.on_openai_event({"type": "input_audio_buffer.speech_started"}, state)
    kinds = [(d, p.get("type") or p.get("event")) for d, p in cmds]
    assert ("openai", "conversation.item.truncate") in kinds
    assert ("twilio", "clear") in kinds
    assert ("openai", "response.cancel") in kinds
    # 聞こえたのは 400ms 分だとモデルに正確に伝える
    trunc = [p for d, p in cmds if p.get("type") == "conversation.item.truncate"][0]
    assert trunc["item_id"] == "item_1"
    assert trunc["audio_end_ms"] == 400
    # 中断後は再生状態をリセット
    assert state["last_assistant_item"] is None


def test_speech_started_without_active_audio_is_noop():
    state = rt.new_state()
    state["stream_sid"] = "ST1"
    cmds = rt.on_openai_event({"type": "input_audio_buffer.speech_started"}, state)
    # 誰も喋っていなければ中断コマンドは出さない（無駄な cancel を避ける）
    assert cmds == []
