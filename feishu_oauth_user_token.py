import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


TENANT_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/index"
USER_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
EXPECTED_REDIRECT_URI = "http://127.0.0.1:8000/callback"
CALLBACK_TIMEOUT_SECONDS = 1800


class OAuthError(Exception):
    """Raised when the OAuth flow fails."""


def require_env(name: str) -> str:
    """Read a required environment variable."""
    value = os.getenv(name)
    if not value:
        raise OAuthError(f"Missing required environment variable: {name}")
    return value


def validate_redirect_uri(redirect_uri: str) -> None:
    """Ensure the redirect URI matches the local callback server."""
    if redirect_uri != EXPECTED_REDIRECT_URI:
        raise OAuthError(
            f"FEISHU_REDIRECT_URI must be exactly {EXPECTED_REDIRECT_URI!r}, "
            f"but got {redirect_uri!r}"
        )


def http_post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    """Send a JSON POST request and return the parsed JSON response."""
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)

    request = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset("utf-8")
            body = response.read().decode(charset)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"HTTP {exc.code} calling {url}: {error_body}") from exc
    except URLError as exc:
        raise OAuthError(f"Network error calling {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OAuthError(f"Invalid JSON returned by {url}: {body}") from exc


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """Call the tenant_access_token endpoint."""
    payload = {"app_id": app_id, "app_secret": app_secret}
    response = http_post_json(TENANT_ACCESS_TOKEN_URL, payload)

    if response.get("code") != 0:
        raise OAuthError(f"tenant_access_token request failed: {json.dumps(response, ensure_ascii=False)}")

    tenant_access_token = response.get("tenant_access_token")
    if not tenant_access_token:
        raise OAuthError(f"tenant_access_token missing in response: {json.dumps(response, ensure_ascii=False)}")

    return tenant_access_token


def build_authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    """Build the Feishu authorize URL."""
    query = urlencode(
        {
            "app_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def start_callback_server():
    """Start a temporary local callback server and return its state."""
    result: dict[str, str | None] = {
        "code": None,
        "state": None,
        "error": None,
        "error_description": None,
    }
    event = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404, "Not Found")
                return

            params = parse_qs(parsed.query)
            result["code"] = params.get("code", [None])[0]
            result["state"] = params.get("state", [None])[0]
            result["error"] = params.get("error", [None])[0]
            result["error_description"] = params.get("error_description", [None])[0]

            success = result["code"] is not None and result["error"] is None
            status_code = 200 if success else 400
            html = (
                "<html><body><h3>OAuth callback received.</h3>"
                "<p>You can close this window and return to the terminal.</p>"
                "</body></html>"
            )
            if not success:
                html = (
                    "<html><body><h3>OAuth callback failed.</h3>"
                    "<p>Check the terminal for details.</p>"
                    "</body></html>"
                )

            self.send_response(status_code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            event.set()

        def log_message(self, format, *args):
            return

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    try:
        server = ReusableHTTPServer(("127.0.0.1", 8000), CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            "Failed to start local callback server on http://127.0.0.1:8000/callback. "
            "Make sure port 8000 is free."
        ) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, event, result


def exchange_user_access_token(tenant_access_token: str, code: str, redirect_uri: str) -> dict:
    """Exchange the authorization code for user_access_token and refresh_token."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    response = http_post_json(USER_ACCESS_TOKEN_URL, payload, headers=headers)

    if response.get("code") != 0:
        raise OAuthError(f"user_access_token request failed: {json.dumps(response, ensure_ascii=False)}")

    data = response.get("data") or {}
    user_access_token = data.get("access_token") or data.get("user_access_token")
    refresh_token = data.get("refresh_token")
    if not user_access_token or not refresh_token:
        raise OAuthError(f"access_token or refresh_token missing in response: {json.dumps(response, ensure_ascii=False)}")

    return response


def refresh_user_access_token(tenant_access_token: str, refresh_token: str) -> dict:
    """Refresh the user_access_token with a refresh_token."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    response = http_post_json(USER_ACCESS_TOKEN_URL, payload, headers=headers)

    if response.get("code") != 0:
        raise OAuthError(f"refresh access_token request failed: {json.dumps(response, ensure_ascii=False)}")

    data = response.get("data") or {}
    user_access_token = data.get("access_token") or data.get("user_access_token")
    next_refresh_token = data.get("refresh_token")
    if not user_access_token or not next_refresh_token:
        raise OAuthError(f"access_token or refresh_token missing in response: {json.dumps(response, ensure_ascii=False)}")

    return response


def print_result(response: dict) -> None:
    """Print the token result."""
    data = response.get("data") or {}
    user_access_token = data.get("access_token") or data.get("user_access_token")
    refresh_token = data.get("refresh_token")

    print("\n=== OAuth Result ===")
    print(f"user_access_token: {user_access_token}")
    print(f"refresh_token: {refresh_token}")

    if "expires_in" in data:
        print(f"expires_in: {data['expires_in']}")
    if "refresh_token_expires_in" in data:
        print(f"refresh_token_expires_in: {data['refresh_token_expires_in']}")

    print("\nRaw response:")
    print(json.dumps(response, indent=2, ensure_ascii=False))


def get_user_access_token_interactively() -> dict:
    """Run the full OAuth flow and return the token response."""
    app_id = require_env("FEISHU_APP_ID")
    app_secret = require_env("FEISHU_APP_SECRET")
    redirect_uri = require_env("FEISHU_REDIRECT_URI")
    validate_redirect_uri(redirect_uri)

    print("Requesting tenant_access_token...")
    tenant_access_token = get_tenant_access_token(app_id, app_secret)
    print("tenant_access_token acquired.")

    server, thread, event, callback_result = start_callback_server()
    state = secrets.token_urlsafe(24)
    authorize_url = build_authorize_url(app_id, redirect_uri, state)

    try:
        print("\nOpen this URL in your browser:")
        print(authorize_url)
        print(f"\nWaiting for callback on {EXPECTED_REDIRECT_URI} ...")

        if not event.wait(CALLBACK_TIMEOUT_SECONDS):
            raise OAuthError(
                f"Timed out waiting for callback after {CALLBACK_TIMEOUT_SECONDS} seconds."
            )

        if callback_result["error"]:
            raise OAuthError(
                f"Authorization failed: {callback_result['error']} "
                f"{callback_result['error_description'] or ''}".strip()
            )

        code = callback_result["code"]
        if not code:
            raise OAuthError("Callback did not contain a code parameter.")
        if callback_result["state"] != state:
            raise OAuthError("State mismatch in callback.")

        print("Authorization code received. Requesting user_access_token...")
        token_response = exchange_user_access_token(tenant_access_token, code, redirect_uri)
        return token_response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main() -> int:
    """Run the full OAuth flow."""
    token_response = get_user_access_token_interactively()
    print_result(token_response)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
