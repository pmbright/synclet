import base64
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

# Load local credentials without committing them to source control.
load_dotenv(Path(__file__).resolve().parent / ".env")

# =========================
# CONFIG
# =========================
CLIENT_ID = os.environ["QBO_CLIENT_ID"]
CLIENT_SECRET = os.environ["QBO_CLIENT_SECRET"]
REDIRECT_URI = "http://localhost:8000/callback"
SCOPE = "com.intuit.quickbooks.accounting"

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

TOKEN_FILE = "qbo_tokens.json"


# =========================
# HELPERS
# =========================
def build_auth_url():
    return (
        f"{AUTH_URL}?"
        f"client_id={CLIENT_ID}&"
        f"response_type=code&"
        f"scope={SCOPE}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"state=test123"
    )


def get_basic_auth_header():
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def save_tokens(tokens, realm_id):
    data = {
        "realmId": realm_id,
        **tokens
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n✅ Tokens saved to {TOKEN_FILE}")


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


# =========================
# TOKEN EXCHANGE
# =========================
def exchange_code_for_tokens(code, realm_id):
    print("\n🔄 Exchanging code for tokens...")

    headers = {
        **get_basic_auth_header(),
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(TOKEN_URL, headers=headers, data=data)

    print(f"Status: {response.status_code}")
    print("intuit_tid:", response.headers.get("intuit_tid"))

    response.raise_for_status()

    tokens = response.json()
    save_tokens(tokens, realm_id)


# =========================
# REFRESH TOKEN
# =========================
def refresh_tokens():
    print("\n🔄 Refreshing tokens...")

    tokens = load_tokens()

    headers = {
        **get_basic_auth_header(),
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"]
    }

    response = requests.post(TOKEN_URL, headers=headers, data=data)

    print(f"Status: {response.status_code}")
    print("intuit_tid:", response.headers.get("intuit_tid"))

    response.raise_for_status()

    new_tokens = response.json()
    save_tokens(new_tokens, tokens["realmId"])


# =========================
# LOCAL CALLBACK SERVER
# =========================
class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)

        code = params.get("code", [None])[0]
        realm_id = params.get("realmId", [None])[0]

        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"You can close this window now.")

        print("\n✅ Callback received")
        print("Code:", code)
        print("RealmId:", realm_id)

        # Exchange tokens in a separate thread so server can finish response
        threading.Thread(target=exchange_code_for_tokens, args=(code, realm_id)).start()


def start_server():
    server = HTTPServer(("localhost", 8000), CallbackHandler)
    print("🌐 Listening on http://localhost:8000/callback")
    server.handle_request()  # handle ONE request then exit


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    print("1. Get new tokens")
    print("2. Refresh tokens")
    choice = input("Choose option: ").strip()

    if choice == "1":
        auth_url = build_auth_url()
        print("\n👉 Open this URL in your browser:\n")
        print(auth_url)

        # Optional: auto-open browser
        webbrowser.open(auth_url)

        start_server()

    elif choice == "2":
        refresh_tokens()

    else:
        print("Invalid option")
