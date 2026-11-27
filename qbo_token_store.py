import base64
import json
import os
import time
from pathlib import Path

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


def load_tokens():
    with TOKEN_FILE.open("r") as f:
        return json.load(f)


def save_tokens(tokens):
    with TOKEN_FILE.open("w") as f:
        json.dump(tokens, f, indent=2)


def refresh_tokens(tokens):
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {_basic_auth_header()}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
        timeout=30,
    )

    response.raise_for_status()

    new_tokens = response.json()

    new_tokens["expires_at"] = int(time.time()) + int(new_tokens["expires_in"])

    # Support either naming style
    new_tokens["realm_id"] = tokens.get("realm_id") or tokens.get("realmId")

    save_tokens(new_tokens)

    return new_tokens


def get_valid_tokens():
    tokens = load_tokens()

    expires_at = tokens.get("expires_at")

    # If your old token file lacks expires_at, refresh immediately
    if not expires_at or expires_at < int(time.time()) + 300:
        tokens = refresh_tokens(tokens)

    return tokens


def get_access_token():
    return get_valid_tokens()["access_token"]


def get_realm_id():
    tokens = get_valid_tokens()
    return tokens.get("realm_id") or tokens.get("realmId")

