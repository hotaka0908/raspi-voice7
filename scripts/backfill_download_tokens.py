#!/usr/bin/env python3
"""
既存のFirebase Storage URLにダウンロードトークンを付与するバックフィルスクリプト

storage.rules を auth != null に締めると、トークンなしの ?alt=media URLは
スマホ側の<img>等から403になる。アップロード時に自動生成済みのトークンを
メタデータから取得し、RTDBに保存されたURL（lifelogsのphotoUrl、messagesの
audio_url/photo_url、detail_infoのimageUrl）へ &token= を付け直す。

使い方:
    ~/.ai-necklace/.env に FIREBASE_AUTH_EMAIL / FIREBASE_AUTH_PASSWORD を設定後、
    python scripts/backfill_download_tokens.py --dry-run   # 確認
    python scripts/backfill_download_tokens.py             # 実行
"""

import os
import sys
import argparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from core import firebase_auth  # noqa: E402

load_dotenv(os.path.expanduser("~/.ai-necklace/.env"))
DB_URL = os.getenv("FIREBASE_DATABASE_URL", "").rstrip("/")


def add_token(url):
    """トークンなしのStorage URLにダウンロードトークンを付与。不要・不可ならNone"""
    if not url or "token=" in url or "firebasestorage.googleapis.com" not in url:
        return None
    meta_url = url.split("?")[0]
    resp = requests.get(meta_url, headers=firebase_auth.storage_auth_headers(), timeout=15)
    if resp.status_code != 200:
        print(f"  ! メタデータ取得失敗 {resp.status_code}: {meta_url}")
        return None
    token = (resp.json().get("downloadTokens") or "").split(",")[0]
    if not token:
        print(f"  ! ダウンロードトークン未設定: {meta_url}")
        return None
    return f"{meta_url}?alt=media&token={token}"


def db_get(path):
    resp = requests.get(f"{DB_URL}/{path}.json",
                        params=firebase_auth.db_auth_params(), timeout=30)
    resp.raise_for_status()
    return resp.json() or {}


def db_set(path, value, dry_run):
    if dry_run:
        print(f"  [dry-run] {path}")
        return
    resp = requests.put(f"{DB_URL}/{path}.json", json=value,
                        params=firebase_auth.db_auth_params(), timeout=15)
    resp.raise_for_status()
    print(f"  更新: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="更新せず対象のみ表示")
    args = parser.parse_args()

    if not DB_URL:
        sys.exit("FIREBASE_DATABASE_URL が設定されていません")
    if not firebase_auth.is_configured():
        sys.exit("FIREBASE_AUTH_EMAIL / FIREBASE_AUTH_PASSWORD が設定されていません")
    if not firebase_auth.get_id_token():
        sys.exit("Firebaseサインインに失敗しました")

    updated = 0

    print("== lifelogs ==")
    for date, entries in db_get("lifelogs").items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            new_url = add_token(entry.get("photoUrl"))
            if new_url:
                db_set(f"lifelogs/{date}/{key}/photoUrl", new_url, args.dry_run)
                updated += 1

    print("== messages ==")
    for key, msg in db_get("messages").items():
        if not isinstance(msg, dict):
            continue
        for field in ("audio_url", "photo_url"):
            new_url = add_token(msg.get(field))
            if new_url:
                db_set(f"messages/{key}/{field}", new_url, args.dry_run)
                updated += 1

    print("== detail_info ==")
    for key, info in db_get("detail_info").items():
        if not isinstance(info, dict):
            continue
        new_url = add_token(info.get("imageUrl"))
        if new_url:
            db_set(f"detail_info/{key}/imageUrl", new_url, args.dry_run)
            updated += 1

    suffix = "（dry-run）" if args.dry_run else ""
    print(f"完了: {updated} 件のURLを更新{suffix}")


if __name__ == "__main__":
    main()
