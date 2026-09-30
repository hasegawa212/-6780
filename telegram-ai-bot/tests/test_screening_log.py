"""電話5問 → 仮ランク（sales-rank）をさくらに組み込むテスト（TDD）。

判定は sales-rank の 1 か所に置き、TAC はそれを呼んで記録するだけ（判定を二重化しない）。
さくらは「相手が会話の中で自分から話した内容」だけを記録し、聞き出さない。年収は扱わない。
仮ランクは社内の判断材料で、相手には伝えない（道具の返り値に含めない）。既定 OFF。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tac import console, screening_log  # noqa: E402
from tac.config import CONFIG  # noqa: E402
from tac.models import Channel, Conversation  # noqa: E402
from tac.tools import build_default_registry  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GOOD = {"rent_yen": 65000, "tenure_years": 5, "job_change": "なし",
        "decision_maker_present": True, "has_motivation": True}


def _sales_rank():
    """比較用に sales-rank の screening を直接読む（判定が同じになることの検算）。"""
    spec = importlib.util.spec_from_file_location("sr_screening_for_test", REPO / "sales-rank" / "screening.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass が自分のモジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cfg():
    saved = (CONFIG.screening_enabled, CONFIG.screening_file)
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.close()
    CONFIG.screening_file = f.name
    CONFIG.screening_enabled = False
    yield CONFIG
    CONFIG.screening_enabled, CONFIG.screening_file = saved


# --- 判定は sales-rank と同じ答え ----------------------------------------------------


def test_sales_rank_dir_is_found_from_repo_layout():
    assert (screening_log.sales_rank_dir() / "screening.py").is_file()


@pytest.mark.parametrize("answers", [
    GOOD,
    {**GOOD, "tenure_years": 0.5},
    {**GOOD, "job_change": "転職を検討中"},
    {**GOOD, "rent_yen": 0, "has_motivation": False},
    {**GOOD, "decision_maker_present": False},
    {"rent_yen": 80000},
    {},
])
def test_evaluate_matches_sales_rank(answers):
    sr = _sales_rank()
    job = {j.value: j for j in sr.JobChange}.get(answers.get("job_change"))
    expected = sr.provisional_rank(sr.PhoneAnswers(
        rent_yen=answers.get("rent_yen"), tenure_years=answers.get("tenure_years"), job_change=job,
        decision_maker_present=answers.get("decision_maker_present"), has_motivation=answers.get("has_motivation")))
    got = screening_log.evaluate(answers)
    assert got["rank"] == expected.rank.value
    assert got["reasons"] == expected.reasons
    assert got["missing"] == expected.missing


def test_evaluate_known_ranks():
    assert screening_log.evaluate(GOOD)["rank"] == "A"
    assert screening_log.evaluate({**GOOD, "tenure_years": 0.5})["rank"] == "保留"
    assert screening_log.evaluate({**GOOD, "rent_yen": 0, "has_motivation": False})["rank"] == "見送り"


@pytest.mark.parametrize("label", ["なし", "転勤の可能性あり", "転職を検討中"])
def test_job_change_labels_are_understood(label):
    assert screening_log.evaluate({"job_change": label})["answers"]["job_change"] == label


@pytest.mark.parametrize("bad", [
    {"rent_yen": "六万"}, {"rent_yen": True}, {"rent_yen": -1}, {"tenure_years": -2},
    {"tenure_years": 99}, {"job_change": "たぶん転職"}, {"decision_maker_present": "yes"},
    {"has_motivation": 1},
])
def test_invalid_values_become_unknown(bad):
    [key] = bad
    got = screening_log.evaluate(bad)
    assert got["answers"][key] is None
    assert got["ignored"] == [key]
    assert key in got["missing"]


def test_numbers_are_normalized():
    got = screening_log.evaluate({"rent_yen": 65000.0, "tenure_years": 3})
    assert got["answers"]["rent_yen"] == 65000 and isinstance(got["answers"]["rent_yen"], int)
    assert got["answers"]["tenure_years"] == 3.0


# --- 記録 --------------------------------------------------------------------------


def test_append_and_recent_roundtrip_and_skip_broken_lines(cfg):
    screening_log.append("CA1", "+819011112222", screening_log.evaluate(GOOD))
    with open(cfg.screening_file, "ab") as f:
        f.write(b"[]\n\xff broken\n")
    screening_log.append("CA2", "+819033334444", screening_log.evaluate({**GOOD, "tenure_years": 0.5}))
    got = screening_log.recent()
    assert [(r["call_sid"], r["caller"], r["rank"]) for r in got] == [
        ("CA2", "+819033334444", "保留"), ("CA1", "+819011112222", "A")]
    assert "ts" in got[0]


# --- さくらの道具 --------------------------------------------------------------------


def _registry():
    conv = Conversation(sid="CA-test", channel=Channel.VOICE, customer_id="+819055556666")
    return build_default_registry(handoff_manager=None, conversation_getter=lambda: conv)


def test_tool_absent_when_disabled(cfg):
    assert not _registry().has("record_screening")


def test_tool_records_without_revealing_rank(cfg):
    cfg.screening_enabled = True
    reg = _registry()
    assert reg.has("record_screening")
    out = reg.call("record_screening", **GOOD)
    assert out == {"recorded": True}  # ランクも理由も返さない＝相手に伝わらない
    [rec] = screening_log.recent()
    assert rec["call_sid"] == "CA-test" and rec["caller"] == "+819055556666" and rec["rank"] == "A"


def test_tool_schema_never_asks_income_and_says_do_not_probe(cfg):
    cfg.screening_enabled = True
    [spec] = [s for s in _registry().specs() if s["name"] == "record_screening"]
    props = spec["input_schema"]["properties"]
    assert set(props) == {"rent_yen", "tenure_years", "job_change", "decision_maker_present", "has_motivation"}
    assert spec["input_schema"].get("required", []) == []
    assert "聞き出さない" in spec["description"] and "伝えない" in spec["description"]
    assert props["job_change"]["enum"] == ["なし", "転勤の可能性あり", "転職を検討中"]


# --- コンソール ---------------------------------------------------------------------


def test_console_shows_screenings_escaped():
    rec = {"ts": "2026-09-30T02:00:00+00:00", "caller": "<+8190>", "rank": "B",
           "reasons": ["家賃が未確認"], "missing": ["rent_yen"]}
    page = console.render(summary={}, calls=[], dnc_numbers=[], screenings=[rec])
    assert "電話5問" in page and "B" in page and "家賃が未確認" in page
    assert "&lt;+8190&gt;" in page and "<+8190>" not in page


def test_console_without_screenings_has_no_section():
    assert "電話5問" not in console.render(summary={}, calls=[], dnc_numbers=[])


# --- 本番イメージ（sales-rank を含め、秘密情報・記録は含めない） ---------------------------


def test_dockerignore_allowlists_code_and_excludes_secrets():
    lines = [ln.strip() for ln in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert lines[0] == "*"  # 既定はすべて除外
    allow = [ln for ln in lines if ln.startswith("!")]
    assert set(allow) == {"!telegram-ai-bot/tac/", "!sales-rank/*.py"}
    last_allow = max(lines.index(a) for a in allow)
    for secret in ("telegram-ai-bot/tac/.env", "telegram-ai-bot/tac/.env.*",
                   "telegram-ai-bot/tac/*.jsonl", "telegram-ai-bot/tac/dnc.txt"):
        assert secret in lines and lines.index(secret) > last_allow  # 戻した後で除外し直す


def test_dockerfile_copies_sales_rank_in_repo_layout():
    text = (REPO / "telegram-ai-bot" / "tac" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY sales-rank/ /app/sales-rank/" in text
    assert "COPY telegram-ai-bot/tac /app/telegram-ai-bot/tac" in text
    assert "WORKDIR /app/telegram-ai-bot" in text


def test_screening_records_are_gitignored():
    ignored = (REPO / "telegram-ai-bot" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "tac/screenings.jsonl" in ignored  # 電話番号と5問の答えを含むので GitHub に上げない
