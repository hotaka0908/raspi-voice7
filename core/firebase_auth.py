"""
Firebase Authentication Module (REST API)

メール/パスワードでFirebase Authにサインインし、IDトークンを管理する。
Realtime Database / Cloud Storage のセキュリティルールを
auth != null に締めた状態でデバイスからアクセスするために使用。

FIREBASE_AUTH_EMAIL / FIREBASE_AUTH_PASSWORD が未設定の場合は無効
（トークンなし＝従来どおりの無認証アクセス）。
"""

import os
import re
import time
import logging
import threading
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)

logger = logging.getLogger("firebase_auth")

_API_KEY = os.getenv("FIREBASE_API_KEY", "")
_EMAIL = os.getenv("FIREBASE_AUTH_EMAIL", "")
_PASSWORD = os.getenv("FIREBASE_AUTH_PASSWORD", "")

_lock = threading.Lock()
_id_token: Optional[str] = None
_refresh_token: Optional[str] = None
_expires_at: float = 0.0

# 期限の5分前には更新する
_REFRESH_MARGIN = 300


def is_configured() -> bool:
    """認証情報が設定されているか"""
    return bool(_API_KEY and _EMAIL and _PASSWORD)


def _sign_in() -> bool:
    global _id_token, _refresh_token, _expires_at
    url = ("https://identitytoolkit.googleapis.com/v1/"
           f"accounts:signInWithPassword?key={_API_KEY}")
    try:
        response = requests.post(url, json={
            "email": _EMAIL,
            "password": _PASSWORD,
            "returnSecureToken": True,
        }, timeout=10)
        if response.status_code != 200:
            logger.error(f"Firebaseサインイン失敗: {response.status_code} {response.text[:200]}")
            return False
        data = response.json()
        _id_token = data["idToken"]
        _refresh_token = data.get("refreshToken")
        _expires_at = time.time() + int(data.get("expiresIn", 3600))
        logger.info("Firebaseサインイン成功")
        return True
    except Exception as e:
        logger.error(f"Firebaseサインインエラー: {e}")
        return False


def _refresh() -> bool:
    global _id_token, _refresh_token, _expires_at
    if not _refresh_token:
        return _sign_in()
    url = f"https://securetoken.googleapis.com/v1/token?key={_API_KEY}"
    try:
        response = requests.post(url, data={
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token,
        }, timeout=10)
        if response.status_code != 200:
            logger.warning(f"トークン更新失敗、再サインインします: {response.status_code}")
            return _sign_in()
        data = response.json()
        _id_token = data["id_token"]
        _refresh_token = data.get("refresh_token", _refresh_token)
        _expires_at = time.time() + int(data.get("expires_in", 3600))
        return True
    except Exception as e:
        logger.error(f"トークン更新エラー: {e}")
        return False


def get_id_token() -> Optional[str]:
    """有効なIDトークンを返す。認証未設定・取得失敗時はNone。"""
    if not is_configured():
        return None
    with _lock:
        if _id_token and time.time() < _expires_at - _REFRESH_MARGIN:
            return _id_token
        if _id_token:
            _refresh()
        else:
            _sign_in()
        return _id_token


def db_auth_params() -> Dict[str, str]:
    """Realtime Database REST用の認証クエリパラメータ"""
    token = get_id_token()
    return {"auth": token} if token else {}


def storage_auth_headers() -> Dict[str, str]:
    """Cloud Storage REST用の認証ヘッダー"""
    token = get_id_token()
    return {"Authorization": f"Firebase {token}"} if token else {}


def mask_auth_token(text) -> str:
    """ログ出力用: 例外メッセージ等のURLに含まれる認証トークンをマスクする"""
    return re.sub(r"\?auth=[^\s)\"']+", "?auth=***", str(text))
