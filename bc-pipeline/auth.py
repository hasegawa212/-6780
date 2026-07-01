"""複数ユーザー用の軽量認証（標準ライブラリのみ・追加依存なし）。

- パスワードは pbkdf2_hmac(sha256) でソルト付きハッシュ化して `users.json` に保存。
- セッションは HMAC 署名付きクッキー（改ざん不可・有効期限つき）。サーバ側保存は不要。
- **後方互換**: ユーザーが1人も登録されていなければ認証は無効（従来どおり誰でも使える）。
  `users.json`（= BC_USERS_FILE）にユーザーを追加した時点で自動的に認証必須になる。

環境変数:
  BC_USERS_FILE       ユーザー台帳のパス（既定 users.json）
  BC_SESSION_SECRET   クッキー署名鍵（未設定なら台帳の隣に .session_secret を自動生成）
  BC_AUTH_REQUIRED=1  ユーザー未登録でも認証を必須にする（誤設定でのザル状態を防ぐ）
  BC_SESSION_TTL_H    セッション有効時間（時間・既定12）
  BC_COOKIE_SECURE=1  Secure 属性を付ける（HTTPS 公開時のみ。LAN の http では付けない）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

_ALGO = "pbkdf2_sha256"
_ITER = 200_000
COOKIE_NAME = "bc_session"


# ── ユーザー台帳 ─────────────────────────────────────────────
def _users_path() -> Path:
    return Path(os.environ.get("BC_USERS_FILE", "users.json"))


def load_users() -> dict[str, dict[str, Any]]:
    """{username: {pw_hash, display_name, role}} を返す（無ければ空）。"""
    p = _users_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 破損時は空扱い（ログインさせない安全側）
        return {}


def save_users(users: dict[str, dict[str, Any]]) -> None:
    p = _users_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)  # 台帳は本人のみ読める権限に
    except OSError:
        pass


# ── パスワードハッシュ ───────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return f"{_ALGO}${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


# ── セッション（HMAC署名クッキー）────────────────────────────
def _secret() -> bytes:
    env = os.environ.get("BC_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    # 未設定なら台帳の隣に鍵を作って永続化（再起動でセッションが切れないように）。
    p = _users_path().parent / ".session_secret"
    if p.exists():
        return p.read_bytes()
    sec = secrets.token_bytes(32)
    try:
        p.write_bytes(sec)
        os.chmod(p, 0o600)
    except OSError:
        pass
    return sec


def _ttl_seconds() -> int:
    try:
        return int(float(os.environ.get("BC_SESSION_TTL_H", "12")) * 3600)
    except ValueError:
        return 12 * 3600


def _b64e(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _b64d(raw: bytes) -> bytes:
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def create_session(username: str) -> str:
    payload = {"u": username, "exp": int(time.time()) + _ttl_seconds()}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), body, hashlib.sha256).digest())
    return (body + b"." + sig).decode("ascii")


def verify_session(token: str | None) -> str | None:
    """有効なトークンならユーザー名を返す。無効・期限切れ・削除済みユーザーは None。"""
    if not token or "." not in token:
        return None
    try:
        body, sig = token.encode("ascii").split(b".", 1)
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig)):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        user = payload.get("u")
        if not user or user not in load_users():
            return None
        return user
    except Exception:  # noqa: BLE001
        return None


def authenticate(username: str, password: str) -> bool:
    rec = load_users().get(username)
    return bool(rec) and verify_password(password, rec.get("pw_hash", ""))


# ── 有効判定・ユーザー情報 ───────────────────────────────────
def is_enabled() -> bool:
    """認証を有効にするか。ユーザーが1人でも居る or BC_AUTH_REQUIRED=1 なら有効。"""
    return bool(load_users()) or os.environ.get("BC_AUTH_REQUIRED") == "1"


def display_name(username: str) -> str:
    rec = load_users().get(username) or {}
    return rec.get("display_name") or username


def current_user(cookies: dict[str, str]) -> str | None:
    """リクエストのクッキーからログイン中ユーザー名を返す（未ログインは None）。"""
    return verify_session(cookies.get(COOKIE_NAME))


def is_secure_cookie() -> bool:
    return os.environ.get("BC_COOKIE_SECURE") == "1"
