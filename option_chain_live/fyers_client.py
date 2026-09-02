
"""
fyers_client.py - Live NSE option chain via the official Fyers Trading API (v3).

Auth flow (one-time interactive OAuth, token cached):
  generate-authcode (browser) -> login (client ID + 4-digit PIN / QR / OTP)
  -> redirect_uri?auth_code=... -> POST validate-authcode -> access+refresh token.
  Refresh token keeps the session alive without re-login.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from .models import OptionChain, ChainRow, ContractSide, ChainTotals, SideTotals

API_BASE = "https://api-t1.fyers.in/api/v3"
DATA_BASE = "https://api-t1.fyers.in/data"

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN_FILE = os.path.join(THIS_DIR, "..", ".fyers_credentials.json")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Content-Type": "application/json",
}


class FyersError(RuntimeError):
    """Fyers API or auth failure."""


def app_id_hash(app_id: str, secret: str) -> str:
    """SHA-256 of app_id:secret (as required by validate-authcode)."""
    return hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()


def jwt_exp(token: Optional[str]) -> Optional[datetime]:
    """Decode the `exp` claim of a Fyers JWT (accurate server-side expiry).

    Fyers' own docs say the access token lives ~1 day, but the real lifetime
    is whatever the JWT says (e.g. ~15.7h).  Decoding it beats guessing.
    """
    if not token or not isinstance(token, str) or token.count(".") != 2:
        return None
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except Exception:
        return None



class FyersCredentials:
    """App credentials + persisted token cache."""

    def __init__(
        self,
        app_id: str = "",
        secret: str = "",
        client_id: str = "",
        redirect_uri: str = "https://trade.fyers.in/api-login/redirect-uri/index.html",
        token_file: Optional[str] = None,
    ):
        self.app_id = app_id
        self.secret = secret
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file = os.path.abspath(token_file or DEFAULT_TOKEN_FILE)
        self.pin = str(os.environ.get("FYERS_PIN", "") or "")  # 4-digit PIN for token refresh

    # ---- token cache -------------------------------------------------
    def load_tokens(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.token_file):
            return None
        try:
            with open(self.token_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save_tokens(self, access_token: str, refresh_token: Optional[str] = None) -> Dict[str, Any]:
        exp = jwt_exp(access_token)
        expires_at = exp if exp else (datetime.now(timezone.utc) + timedelta(hours=22))
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "issued_at": datetime.now().isoformat(timespec="seconds"),
            "expires_at": expires_at.isoformat(timespec="seconds"),
            "expires_source": "jwt" if exp else "fallback+22h",
            "app_id": self.app_id,
            "client_id": self.client_id,
        }
        r_exp = jwt_exp(refresh_token)
        if r_exp:
            data["refresh_expires_at"] = r_exp.isoformat(timespec="seconds")
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    def access_token(self, session: Optional[requests.Session] = None) -> str:
        """Current access token; raises if missing or expired."""
        t = self.load_tokens()
        if not t or not t.get("access_token"):
            raise FyersError(
                "no Fyers token - run 'python -m option_chain_live fyers-auth' once"
            )
        exp = t.get("expires_at")

        def _expired() -> bool:
            if not exp:
                return False
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                return datetime.now(timezone.utc) >= exp_dt - timedelta(minutes=5)
            except ValueError:
                return False

        if _expired():
            raise FyersError(
                "Fyers access token expired.  The refresh-token API is disabled by Fyers "
                "(SEBI regulation), so a browser re-login is required - run "
                "'python -m option_chain_live fyers-auth' (the pipeline can do this for "
                "you automatically when FYERS_SECRET + FYERS_PIN are configured)."
            )
        return t["access_token"]

    # ---- auth helpers -------------------------------------------------
    def authcode_url(self, state: str = "dsh1") -> str:
        return (
            f"{API_BASE}/generate-authcode?client_id={self.app_id}"
            f"&redirect_uri={self.redirect_uri}&response_type=code&state={state}"
        )


class FyersClient:
    """Bare HTTP client for the Fyers v3 REST API (no third-party SDK)."""

    def __init__(self, creds: Optional[FyersCredentials] = None, timeout: float = 20.0):
        self.creds = creds or FyersCredentials()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    # ---- generic ------------------------------------------------------
    def _request(self, method: str, url: str, body: Optional[dict] = None,
                 headers: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        try:
            r = self.session.request(
                method, url, json=body if body is not None else None,
                headers=headers, params=params, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise FyersError(f"network error: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise FyersError(f"non-JSON from {url}: HTTP {r.status_code}") from e
        return data

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """POST validate-authcode: auth_code -> access/refresh token."""
        data = self._request(
            "POST",
            f"{API_BASE}/validate-authcode",
            {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash(self.creds.app_id, self.creds.secret),
                "code": code,
            },
        )
        if data.get("s") != "ok" or not data.get("access_token"):
            raise FyersError(f"token exchange failed: {json.dumps(data)[:300]}")
        self.creds.save_tokens(data["access_token"], data.get("refresh_token"))
        return data

    def refresh_token(self, pin: Optional[str] = None, creds: Optional[FyersCredentials] = None) -> Dict[str, Any]:
        """POST validate-refresh-token with cached refresh_token + PIN."""
        c = self.creds
        t = c.load_tokens() or {}
        if not t.get("refresh_token"):
            raise FyersError("no refresh token cached")
        if pin is None:
            raise FyersError("refresh needs the Fyers 4-digit PIN (set FYERS_PIN or pass pin=...)")
        data = self._request(
            "POST",
            f"{API_BASE}/validate-refresh-token",
            {
                "grant_type": "refresh_token",
                "appIdHash": app_id_hash(c.app_id, c.secret),
                "refresh_token": t["refresh_token"],
                "pin": str(pin),
            },
        )
        if data.get("s") != "ok" or not data.get("access_token"):
            msg = str(data.get("message") or "")
            code = data.get("code")
            if "disabled" in msg.lower() or code == -16:
                raise FyersError(
                    "Fyers refresh-token API is disabled (SEBI regulation) - token refresh "
                    "cannot happen; run browser re-login: python -m option_chain_live fyers-auth"
                )
            raise FyersError(f"refresh failed: {json.dumps(data)[:300]}")
        c.save_tokens(data["access_token"], data.get("refresh_token") or t["refresh_token"])
        return data

    # ---- auth: access token with one-shot refresh attempt -----------------
    def _token(self) -> str:
        """Current access token; on expiry tries a refresh once (PIN from creds)."""
        try:
            return self.creds.access_token()
        except FyersError as e:
            if "expired" not in str(e).lower():
                raise
            t = self.creds.load_tokens() or {}
            if not t.get("refresh_token") or not self.creds.pin:
                raise
            self.refresh_token(pin=self.creds.pin)
            return self.creds.access_token()

    # ---- market data ----------------------------------------------------
    def option_chain_raw(self, symbol: str, strikecount: int = 20,
                         expiry_ts: Optional[int] = None, greeks: str = "1") -> dict:
        """POST /data/options-chain-v3. symbol like NSE:NIFTY50-INDEX."""
        token = self._token()
        # GET data API names the bank index NIFTYBANK (POST API uses BANKNIFTY).
        if symbol.upper() == "NSE:BANKNIFTY-INDEX":
            symbol = "NSE:NIFTYBANK-INDEX"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "strikecount": strikecount,
            "timestamp": "" if expiry_ts is None else str(expiry_ts),
            "greeks": greeks,
        }
        # NOTE: POST /data/options-chain-v3 is Cloudflare-blocked from non-browser
        # clients; the same endpoint accepts GET with query params.
        data = self._request(
            "GET", f"{DATA_BASE}/options-chain-v3", None,
            headers={"Authorization": token}, params=params,
        )
        if data.get("s") != "ok":
            msg = json.dumps(data)[:300]
            if "expired" in str(data.get("message") or "").lower() or "invalid token" in str(data.get("message") or "").lower():
                msg += " [TOKEN_EXPIRED]"
            raise FyersError(f"option chain failed: {msg}")
        return data

    def quotes(self, symbols: str) -> dict:
        """POST /api/v3/quotes, symbols comma-separated NSE:...-INDEX/EQ."""
        token = self._token()
        data = self._request(
            "POST", f"{API_BASE}/quotes", {"symbols": symbols},
            headers={"Authorization": token},
        )
        if data.get("s") != "ok":
            raise FyersError(f"quotes failed: {json.dumps(data)[:300]}")
        return data

    def expiries(self, symbol: str) -> List[dict]:
        """POST /data/expiry-symbol-list - expiries for an index."""
        token = self.creds.access_token()
        data = self._request(
            "GET", f"{DATA_BASE}/expiry-symbol-list", None,
            headers={"Authorization": token}, params={"symbol": symbol},
        )
        return data.get("data") if isinstance(data.get("data"), list) else []