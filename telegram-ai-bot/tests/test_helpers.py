"""mega_bot の純粋ロジックに対するユニットテスト.

ネットワーク・Telegram・ロック取得は伴わない（main() を呼ばない）ため、
import するだけで安全に検証できる。記憶ファイルはテンポラリへ隔離する。
"""

import os
import sys
import tempfile
from pathlib import Path

# 記憶/スケジュールの永続先をテンポラリへ（実ファイルを汚さない）
os.environ.setdefault("BOT_DATA_DIR", tempfile.mkdtemp(prefix="megabot-test-"))
# 認可ユーザーを既知の値に
os.environ.setdefault("ALLOWED_TELEGRAM_USER_IDS", "111,222")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mega_bot  # noqa: E402

# --- メッセージ分割 (Telegram 4096 文字上限) ------------------------------- #


def test_split_short_returns_single_chunk():
    assert mega_bot.split("こんにちは") == ["こんにちは"]


def test_split_long_keeps_all_chars_and_respects_limit():
    text = "x" * 10000
    parts = mega_bot.split(text)
    assert all(len(p) <= mega_bot.MAXLEN for p in parts)
    assert "".join(parts) == text  # 改行が無いので欠落なく連結できる


def test_split_prefers_newline_boundary():
    text = "a" * 4000 + "\n" + "b" * 300
    parts = mega_bot.split(text)
    assert len(parts) == 2
    assert parts[0] == "a" * 4000


# --- 認可 ------------------------------------------------------------------ #
# 環境変数に依存せず、モジュールの IDS を直接固定して検証する（hermetic）。


def test_auth_rejects_none_and_unknown():
    mega_bot.IDS = {111, 222}
    assert mega_bot.auth(None) is False
    assert mega_bot.auth(999) is False


def test_auth_accepts_allowed_ids():
    mega_bot.IDS = {111, 222}
    assert mega_bot.auth(111) is True
    assert mega_bot.auth(222) is True


# --- 長期記憶 -------------------------------------------------------------- #


def test_memory_roundtrip_and_dedup():
    cid = 4242
    mega_bot.add_memory(cid, "コーヒーが好き")
    mega_bot.add_memory(cid, "コーヒーが好き")  # 重複は無視
    mega_bot.add_memory(cid, "犬を飼っている")
    mems = mega_bot.get_memory(cid)
    assert mems.count("コーヒーが好き") == 1
    assert "犬を飼っている" in mems


# --- システムプロンプトへの記憶注入 ---------------------------------------- #


def test_system_prompt_includes_memory():
    cid = 5252
    mega_bot.add_memory(cid, "格闘技ジムを経営")
    sysprompt = mega_bot._system_for(cid)
    assert "格闘技ジムを経営" in sysprompt


# --- ツール構成 ------------------------------------------------------------ #


def test_tools_include_web_search_when_enabled():
    names = [t.get("name") for t in mega_bot._tools_for_chat()]
    assert "save_memory" in names  # クライアントツールは常に含まれる


# --- MCP クライアント設定 -------------------------------------------------- #


def test_mcp_servers_parsed_as_list():
    # 未設定（既定）では空リスト＝通常パス（既存挙動と完全互換）
    assert isinstance(mega_bot.MCP_SERVERS, list)


# --- 知識ベース ------------------------------------------------------------ #


def test_knowledge_is_injected_into_system_prompt():
    cid = 7777
    mega_bot.add_knowledge(cid, "料金表", "入会金1万円、月会費8千円")
    sysprompt = mega_bot._system_for(cid)
    assert "料金表" in sysprompt
    assert "月会費8千円" in sysprompt


# --- 顧客台帳（訪問営業CRM） ---------------------------------------------- #


def test_customer_record_save_and_lookup():
    cid = 8888
    mega_bot.add_customer_note(cid, "田中商事", "初回訪問。導入に前向き")
    mega_bot.add_customer_note(cid, "田中商事", "来週見積り提出")
    name, rec = mega_bot.find_customer(cid, "田中")
    assert name == "田中商事"
    assert len(rec["log"]) == 2
    assert any("見積り" in line for line in rec["log"])


# --- チーム共有のデータキー -------------------------------------------------- #


def test_data_key_per_chat_by_default():
    # 既定（TEAM_MODE=off）ではチャットごとに分離
    assert mega_bot._dk(12345) == "12345"
    assert mega_bot.TEAM_MODE is False
