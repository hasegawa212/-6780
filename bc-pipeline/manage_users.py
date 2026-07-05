#!/usr/bin/env python3
"""BC自動生成アプリのユーザー管理CLI（複数ユーザー運用の初期設定・保守用）。

  python manage_users.py add    <username> [--name 表示名] [--role role]
  python manage_users.py passwd <username>
  python manage_users.py remove <username>
  python manage_users.py list

パスワードは対話プロンプトで入力（画面に出ない）。環境変数 BC_NEW_PASSWORD が
あればそれを使う（非対話・スクリプト用）。台帳は BC_USERS_FILE（既定 users.json）。
ユーザーを1人でも追加するとアプリは自動的にログイン必須になる。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import auth


def _prompt_password() -> str:
    env = os.environ.get("BC_NEW_PASSWORD")
    if env:
        return env
    pw = getpass.getpass("パスワード: ")
    if pw != getpass.getpass("パスワード（確認）: "):
        sys.exit("パスワードが一致しません。")
    if len(pw) < 8:
        sys.exit("パスワードは8文字以上にしてください。")
    return pw


def cmd_add(args: argparse.Namespace) -> None:
    users = auth.load_users()
    if args.username in users:
        sys.exit(f"ユーザー '{args.username}' は既に存在します（passwd で変更可）。")
    users[args.username] = {
        "pw_hash": auth.hash_password(_prompt_password()),
        "display_name": args.name or args.username,
        "role": args.role,
    }
    auth.save_users(users)
    print(f"追加しました: {args.username}（表示名: {users[args.username]['display_name']}）")


def cmd_passwd(args: argparse.Namespace) -> None:
    users = auth.load_users()
    if args.username not in users:
        sys.exit(f"ユーザー '{args.username}' は存在しません。")
    users[args.username]["pw_hash"] = auth.hash_password(_prompt_password())
    auth.save_users(users)
    print(f"パスワードを更新しました: {args.username}")


def cmd_remove(args: argparse.Namespace) -> None:
    users = auth.load_users()
    if users.pop(args.username, None) is None:
        sys.exit(f"ユーザー '{args.username}' は存在しません。")
    auth.save_users(users)
    print(f"削除しました: {args.username}")


def cmd_list(_args: argparse.Namespace) -> None:
    users = auth.load_users()
    if not users:
        print("（ユーザー未登録。認証は無効＝誰でも使える状態です）")
        return
    for name, rec in sorted(users.items()):
        print(f"  {name}\t表示名={rec.get('display_name', name)}\trole={rec.get('role')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="BC自動生成アプリのユーザー管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="ユーザー追加")
    a.add_argument("username")
    a.add_argument("--name", default=None, help="表示名（省略時は username）")
    a.add_argument("--role", default="member", help="役割ラベル（任意）")
    a.set_defaults(func=cmd_add)
    p = sub.add_parser("passwd", help="パスワード変更")
    p.add_argument("username")
    p.set_defaults(func=cmd_passwd)
    r = sub.add_parser("remove", help="ユーザー削除")
    r.add_argument("username")
    r.set_defaults(func=cmd_remove)
    ls = sub.add_parser("list", help="ユーザー一覧")
    ls.set_defaults(func=cmd_list)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
