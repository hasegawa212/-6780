"""監査ログ - 全操作を記録する。個人情報は記録しない。"""

import json, os, time, re
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
AUDIT_FILE = LOG_DIR / "audit.jsonl"

# 個人情報マスキングパターン
_PII_PATTERNS = [
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), '***@***.***'),
    (re.compile(r'\d{2,4}-\d{2,4}-\d{4}'), '***-****-****'),
    (re.compile(r'(?:sk-ant-|sk-proj-|xoxp-|xoxb-)[\w-]+'), '***API_KEY***'),
    (re.compile(r'bc_session=[\w.=-]+'), 'bc_session=***'),
]

def mask_pii(text: str) -> str:
    """個人情報をマスキング"""
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

def log_action(
    action: str,
    user: str = "",
    role: str = "",
    case_id: str = "",
    success: bool = True,
    detail: str = "",
    target: str = "",
    ip: str = "",
):
    """監査ログを1行追記"""
    entry = {
        "ts": datetime.now().isoformat(),
        "action": action,
        "user": user,
        "role": role,
        "case_id": case_id,
        "success": success,
        "detail": mask_pii(detail)[:200],  # 200文字制限
        "target": mask_pii(target),
        "ip": ip,
    }
    with open(AUDIT_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def get_recent(limit: int = 50) -> list[dict]:
    """最新の監査ログを取得"""
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text().strip().split("\n")
    return [json.loads(l) for l in lines[-limit:]]
