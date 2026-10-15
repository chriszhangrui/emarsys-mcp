"""client.py — Robust HTTP client & Auth manager for SAP Emarsys API.

Features:
- OAuth 2.0 Client Credentials Grant with Basic Auth header
- Token caching with automatic proactive renewal before expiration
- Legacy WSSE (UsernameToken) authentication support for older accounts
- Automatic URL rewriting: maps legacy /v2/ paths to modern /api/v3/ under OAuth 2.0
- Path parameter interpolation (:param and {param})
- Rate limit (HTTP 429) & transient error backoff retry with Retry-After header
- Structured JSON error parsing (replyCode, replyText, data.errors)
- Latency tracking & connection diagnostics
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from config import config

logger = logging.getLogger("emarsys.client")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class EmarsysAPIError(Exception):
    """Structured exception for Emarsys API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        reply_code: Optional[int] = None,
        reply_text: Optional[str] = None,
        errors: Optional[Any] = None,
        url: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reply_code = reply_code
        self.reply_text = reply_text
        self.errors = errors
        self.url = url

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.message,
            "status_code": self.status_code,
            "reply_code": self.reply_code,
            "reply_text": self.reply_text,
            "details": self.errors,
            "url": self.url,
        }


class EmarsysClient:
    def __init__(self):
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_profile_hash: Optional[str] = None
        self._token_lock: Optional[asyncio.Lock] = None
        self._lock_loop = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._client_loop = None

    def _get_token_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._token_lock is None or self._lock_loop != loop:
            self._token_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._token_lock

    def _get_http_client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if (
            self._http_client is None
            or self._http_client.is_closed
            or self._client_loop != loop
        ):
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=10.0),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
            self._client_loop = loop
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
            self._client_loop = None

    def _compute_profile_hash(self, cfg: Dict[str, Any]) -> str:
        raw = f"{cfg.get('authType')}:{cfg.get('clientId')}:{cfg.get('clientSecret')}:{cfg.get('username')}:{cfg.get('secret')}:{cfg.get('baseUrl')}:{cfg.get('tokenUrl')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    async def get_valid_token(self, force_refresh: bool = False) -> str:
        """Retrieve valid OAuth2 Bearer token, refreshing if expiring within 60 seconds."""
        cfg = config.resolve()
        profile_hash = self._compute_profile_hash(cfg)
        now = time.time()

        if (
            not force_refresh
            and self._token
            and self._token_profile_hash == profile_hash
            and now < (self._token_expires_at - 60)
        ):
            return self._token

        async with self._get_token_lock():
            # Double-check inside lock
            now = time.time()
            if (
                not force_refresh
                and self._token
                and self._token_profile_hash == profile_hash
                and now < (self._token_expires_at - 60)
            ):
                return self._token

            client_id = cfg.get("clientId")
            client_secret = cfg.get("clientSecret")
            token_url = cfg.get("tokenUrl", "https://auth.emarsys.net/oauth2/token")

            if not client_id or not client_secret:
                raise EmarsysAPIError(
                    f"Missing OAuth2 credentials for tenant '{cfg.get('activeName')}'. "
                    "Please check systems.json or set EMARSYS_CLIENT_ID / EMARSYS_CLIENT_SECRET."
                )

            # Emarsys OAuth2 requires client_secret_basic
            creds = f"{client_id}:{client_secret}".encode("utf-8")
            b64_creds = base64.b64encode(creds).decode("ascii")

            headers = {
                "Authorization": f"Basic {b64_creds}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = "grant_type=client_credentials"

            client = self._get_http_client()
            try:
                resp = await client.post(token_url, headers=headers, content=body, timeout=15.0)
            except Exception as e:
                raise EmarsysAPIError(f"Network error connecting to token endpoint {token_url}: {e}")

            if resp.status_code != 200:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error_description") or err_json.get("error") or err_text
                except Exception:
                    err_msg = err_text
                raise EmarsysAPIError(
                    f"OAuth2 authentication failed (HTTP {resp.status_code}): {err_msg}",
                    status_code=resp.status_code,
                    url=token_url,
                )

            data = resp.json()
            access_token = data.get("access_token")
            expires_in = int(data.get("expires_in", 3600))

            if not access_token:
                raise EmarsysAPIError("OAuth token endpoint response did not contain 'access_token'")

            self._token = access_token
            self._token_expires_at = time.time() + expires_in
            self._token_profile_hash = profile_hash
            logger.info("Successfully acquired Emarsys OAuth2 token (expires in %ds)", expires_in)
            return self._token

    def _generate_wsse_header(self, username: str, secret: str) -> str:
        """Generate WSSE UsernameToken header for legacy authentication."""
        nonce = secrets.token_hex(16)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_digest = f"{nonce}{created}{secret}".encode("utf-8")
        password_digest = base64.b64encode(hashlib.sha1(raw_digest).hexdigest().encode("utf-8")).decode("ascii")
        return f'UsernameToken Username="{username}", PasswordDigest="{password_digest}", Nonce="{nonce}", Created="{created}"'

    def normalize_url(self, raw_path_or_url: str, path_params: Optional[Dict[str, Any]] = None) -> str:
        """Resolve full API URL, interpolate path parameters, and route /v2/ -> /api/v3/ for OAuth2."""
        cfg = config.resolve()
        base_url = cfg.get("baseUrl", "https://api.emarsys.net").rstrip("/")
        auth_type = cfg.get("authType", "oauth2")

        url = raw_path_or_url.strip()

        # Handle postman variable {{baseUrl}}
        url = url.replace("{{baseUrl}}", base_url)

        # Interpolate path params (:param or {param})
        if path_params:
            for k, v in path_params.items():
                str_v = str(v)
                url = re.sub(rf":{re.escape(k)}(?=/|\?|$)", str_v, url)
                url = url.replace(f"{{{k}}}", str_v)

        # If not full URL, prepend base_url
        if not url.startswith("http://") and not url.startswith("https://"):
            if not url.startswith("/"):
                url = "/" + url
            url = f"{base_url}{url}"

        # URL path normalization for OAuth2 vs WSSE:
        # Emarsys OAuth2 uses /api/v3/... while legacy WSSE uses /api/v2/...
        if auth_type == "oauth2":
            # Replace /api/v2/ or /v2/ with /api/v3/
            if "/api/v2/" in url:
                url = url.replace("/api/v2/", "/api/v3/")
            elif f"{base_url}/v2/" in url:
                url = url.replace(f"{base_url}/v2/", f"{base_url}/api/v3/")
            elif url.endswith("/v2"):
                url = url[:-3] + "/api/v3"
            elif not "/api/v3" in url and not "/rds/" in url and not "/campaigns" in url and not "/inbound-messages" in url:
                # If path starts after baseUrl with e.g. /settings -> /api/v3/settings
                parsed_after_base = url[len(base_url):]
                if parsed_after_base and not parsed_after_base.startswith("/api/"):
                    url = f"{base_url}/api/v3{parsed_after_base}"
        else:
            # WSSE mode
            if "/v2/" in url and "/api/v2/" not in url:
                url = url.replace(f"{base_url}/v2/", f"{base_url}/api/v2/")

        return url

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        path_params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Execute robust HTTP request with automatic auth, retry on 429/5xx, and response parsing."""
        cfg = config.resolve()
        auth_type = cfg.get("authType", "oauth2")
        target_url = self.normalize_url(path_or_url, path_params)

        req_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            req_headers.update(headers)

        # Set up Authentication header
        if auth_type == "oauth2":
            token = await self.get_valid_token()
            req_headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "wsse":
            username = cfg.get("username", "")
            secret = cfg.get("secret", "")
            if not username or not secret:
                raise EmarsysAPIError("Missing WSSE username or secret in configuration.")
            req_headers["X-WSSE"] = self._generate_wsse_header(username, secret)

        client = self._get_http_client(timeout=timeout)
        method_upper = method.upper()

        # Clean query parameters: omit None values
        clean_params = None
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}

        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            start_time = time.time()
            try:
                resp = await client.request(
                    method=method_upper,
                    url=target_url,
                    params=clean_params,
                    json=json_data,
                    headers=req_headers,
                    timeout=timeout,
                )
                latency_ms = round((time.time() - start_time) * 1000, 1)

                # Check for rate limiting (429)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (attempt * 2.0)
                    logger.warning(
                        "Emarsys API rate limited (429) on %s %s. Retrying in %.1fs (attempt %d/%d)",
                        method_upper,
                        target_url,
                        sleep_time,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                # Check for server transient errors (500, 502, 503, 504)
                if resp.status_code in (502, 503, 504) and attempt < max_retries:
                    sleep_time = attempt * 1.5
                    logger.warning(
                        "Emarsys API returned %d on %s. Retrying in %.1fs (attempt %d/%d)",
                        resp.status_code,
                        target_url,
                        sleep_time,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                # Parse JSON response
                try:
                    res_json = resp.json() if resp.content else {}
                except Exception:
                    res_json = {"raw": resp.text}

                # Evaluate Emarsys response semantics
                # Standard Emarsys envelope: {"replyCode": 0, "replyText": "OK", "data": ...}
                reply_code = res_json.get("replyCode") if isinstance(res_json, dict) else None
                reply_text = res_json.get("replyText") if isinstance(res_json, dict) else None

                if resp.status_code >= 400:
                    err_msg = (
                        res_json.get("errorMessage")
                        or res_json.get("error_description")
                        or res_json.get("error")
                        or reply_text
                        or resp.reason_phrase
                        or f"HTTP {resp.status_code}"
                    )
                    details = res_json.get("data") or res_json
                    raise EmarsysAPIError(
                        f"Emarsys API Error ({resp.status_code}): {err_msg}",
                        status_code=resp.status_code,
                        reply_code=reply_code,
                        reply_text=reply_text,
                        errors=details,
                        url=target_url,
                    )

                # In some Emarsys endpoints, replyCode != 0 indicates an application-level error even with HTTP 200
                if reply_code is not None and reply_code != 0:
                    raise EmarsysAPIError(
                        f"Emarsys Business Error (replyCode {reply_code}): {reply_text}",
                        status_code=resp.status_code,
                        reply_code=reply_code,
                        reply_text=reply_text,
                        errors=res_json.get("data"),
                        url=target_url,
                    )

                # Return enriched response
                if isinstance(res_json, dict):
                    res_json["_meta"] = {
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "url": target_url,
                        "method": method_upper,
                    }
                return res_json

            except httpx.TimeoutException as e:
                last_err = e
                logger.warning("Timeout (%ss) requesting %s (attempt %d/%d)", timeout, target_url, attempt, max_retries)
                if attempt < max_retries:
                    await asyncio.sleep(attempt * 1.0)
                    continue
                raise EmarsysAPIError(f"Request timed out after {timeout}s: {target_url}", url=target_url)

            except httpx.RequestError as e:
                last_err = e
                logger.warning("Request error on %s: %s (attempt %d/%d)", target_url, str(e), attempt, max_retries)
                if attempt < max_retries:
                    await asyncio.sleep(attempt * 1.0)
                    continue
                raise EmarsysAPIError(f"HTTP request failed: {e}", url=target_url)

        if last_err:
            raise EmarsysAPIError(f"Failed after {max_retries} retries: {last_err}", url=target_url)
        raise EmarsysAPIError(f"Failed to execute request to {target_url}", url=target_url)

    async def get_system_diagnostic(self) -> Dict[str, Any]:
        """Perform a complete health check: credentials, token status, and live ping to API."""
        cfg = config.resolve()
        start = time.time()
        token_valid = False
        expires_in = 0
        token_err = None

        if cfg.get("authType") == "oauth2":
            try:
                token = await self.get_valid_token()
                token_valid = bool(token)
                expires_in = max(0, int(self._token_expires_at - time.time()))
            except Exception as e:
                token_err = str(e)

        # Probe live API: GET /api/v3/settings
        api_ping_ok = False
        api_latency_ms = None
        api_data = None
        api_error = None
        try:
            p_start = time.time()
            resp = await self.request("GET", "/api/v3/settings", timeout=10.0)
            api_latency_ms = round((time.time() - p_start) * 1000, 1)
            api_ping_ok = True
            api_data = resp.get("data")
        except Exception as e:
            api_error = str(e)

        masked_secret = ""
        secret_val = cfg.get("clientSecret") or cfg.get("secret") or ""
        if len(secret_val) > 6:
            masked_secret = secret_val[:3] + "..." + secret_val[-3:]
        elif secret_val:
            masked_secret = "***"

        return {
            "status": "connected" if (api_ping_ok and (token_valid or cfg.get("authType") == "wsse")) else "error",
            "activeSystem": cfg.get("activeName"),
            "tenantLabel": cfg.get("label"),
            "tenantId": cfg.get("tenantId"),
            "authType": cfg.get("authType"),
            "clientId": cfg.get("clientId"),
            "clientSecretMasked": masked_secret,
            "baseUrl": cfg.get("baseUrl"),
            "tokenUrl": cfg.get("tokenUrl"),
            "tokenStatus": {
                "valid": token_valid,
                "expiresInSeconds": expires_in,
                "error": token_err,
            },
            "apiPing": {
                "ok": api_ping_ok,
                "latencyMs": api_latency_ms,
                "error": api_error,
                "settingsPreview": api_data,
            },
            "availableSystems": cfg.get("availableSystems", []),
            "totalCheckLatencyMs": round((time.time() - start) * 1000, 1),
        }


# Global client instance
emarsys_client = EmarsysClient()
