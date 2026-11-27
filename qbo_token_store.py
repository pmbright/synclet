"""Shared, process-safe QBO token storage for invoice export and stock sync."""
import base64
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

import requests
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
TOKEN_FILE = Path(os.getenv("QBO_TOKEN_FILE", "qbo_tokens.json"))
if not TOKEN_FILE.is_absolute():
    TOKEN_FILE = APP_DIR / TOKEN_FILE
QBO_CLIENT_ID = os.environ["QBO_CLIENT_ID"]
QBO_CLIENT_SECRET = os.environ["QBO_CLIENT_SECRET"]


def _basic_auth_header():
    raw = f"{QBO_CLIENT_ID}:{QBO_CLIENT_SECRET}".encode()
    return base64.b64encode(raw).decode()


@contextmanager
def _token_lock():
    # A separate inode is needed because saving replaces the token file atomically.
    fd = os.open(str(TOKEN_FILE) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load_tokens():
    # Atomic replacement means readers see either complete version of the file.
    with TOKEN_FILE.open() as handle:
        return json.load(handle)


def save_tokens(tokens):
    """Atomically save a token response; callers refreshing must hold _token_lock."""
    fd, filename = tempfile.mkstemp(prefix=TOKEN_FILE.name + ".", dir=TOKEN_FILE.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(tokens, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, TOKEN_FILE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def _refresh_tokens(tokens):
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {_basic_auth_header()}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise ValueError("QBO refresh response is missing tokens")
    new_tokens = {**tokens, **payload}
    new_tokens["expires_at"] = int(time.time()) + int(payload["expires_in"])
    new_tokens["realm_id"] = tokens.get("realm_id") or tokens.get("realmId")
    save_tokens(new_tokens)
    return new_tokens


def refresh_tokens(tokens=None):
    # Reload under the lock: a caller may hold a stale copy of a rotated refresh token.
    with _token_lock():
        return _refresh_tokens(load_tokens())


def _expiry_timestamp(value):
    if value is None:
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        # Support the older QuickBooksAPI ISO datetime format as well.
        try:
            return datetime.fromisoformat(value).timestamp()
        except (TypeError, ValueError):
            return 0


def get_valid_tokens(rejected_access_token=None):
    with _token_lock():
        tokens = load_tokens()
        expired = _expiry_timestamp(tokens.get("expires_at")) < time.time() + 300
        rejected = rejected_access_token is not None and tokens.get("access_token") == rejected_access_token
        if not tokens.get("access_token") or expired or rejected:
            tokens = _refresh_tokens(tokens)
        return tokens


def get_access_token():
    return get_valid_tokens()["access_token"]


def get_realm_id():
    tokens = get_valid_tokens()
    return tokens.get("realm_id") or tokens.get("realmId")
