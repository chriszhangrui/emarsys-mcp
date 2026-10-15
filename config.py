"""config.py — Configuration management for Emarsys MCP Server.

Supports:
- Multi-tenant profiles via systems.json
- Environment variable overrides (.env, process env)
- Dynamic runtime configuration and system switching
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
SYSTEMS_FILE = BASE_DIR / "systems.json"
EXAMPLE_FILE = BASE_DIR / "systems.example.json"
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


class EmarsysConfig:
    def __init__(self):
        self._runtime_override: Dict[str, Any] = {}

    def load_systems_file(self) -> Dict[str, Any]:
        target = SYSTEMS_FILE if SYSTEMS_FILE.exists() else EXAMPLE_FILE
        if not target.exists():
            return {"active": "default", "systems": {}}
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"active": "default", "systems": {}}

    def save_systems_file(self, data: Dict[str, Any]) -> None:
        with open(SYSTEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def set_runtime_override(self, **kwargs) -> None:
        """Override configuration fields at runtime during the session."""
        for k, v in kwargs.items():
            if v is not None:
                self._runtime_override[k] = v

    def switch_system(self, system_name: str) -> Dict[str, Any]:
        """Switch active tenant in systems.json."""
        data = self.load_systems_file()
        systems = data.get("systems", {})
        if system_name not in systems:
            available = list(systems.keys())
            raise ValueError(f"System '{system_name}' not found. Available systems: {available}")
        data["active"] = system_name
        self.save_systems_file(data)
        self._runtime_override.clear()
        return {"active": system_name, "system": systems[system_name]}

    def resolve(self) -> Dict[str, Any]:
        """Resolve current active configuration with full hierarchy:
        1. Runtime in-memory overrides (from emarsys_configure)
        2. Environment variables
        3. systems.json active system
        """
        data = self.load_systems_file()
        systems = data.get("systems", {})
        active_name = (
            self._runtime_override.get("active")
            or os.getenv("EMARSYS_ACTIVE_SYSTEM")
            or data.get("active")
            or (list(systems.keys())[0] if systems else "default")
        )
        sys_cfg = systems.get(active_name, {})

        # Resolve fields
        client_id = (
            self._runtime_override.get("clientId")
            or os.getenv("EMARSYS_CLIENT_ID")
            or sys_cfg.get("clientId", "")
        )
        client_secret = (
            self._runtime_override.get("clientSecret")
            or os.getenv("EMARSYS_CLIENT_SECRET")
            or sys_cfg.get("clientSecret", "")
        )
        base_url = (
            self._runtime_override.get("baseUrl")
            or os.getenv("EMARSYS_BASE_URL")
            or sys_cfg.get("baseUrl", "https://api.emarsys.net")
        ).rstrip("/")
        token_url = (
            self._runtime_override.get("tokenUrl")
            or os.getenv("EMARSYS_TOKEN_URL")
            or sys_cfg.get("tokenUrl", "https://auth.emarsys.net/oauth2/token")
        )
        tenant_id = (
            self._runtime_override.get("tenantId")
            or os.getenv("EMARSYS_TENANT_ID")
            or sys_cfg.get("tenantId", "Public_GDM_EMEA037")
        )
        auth_type = (
            self._runtime_override.get("authType")
            or os.getenv("EMARSYS_AUTH_TYPE")
            or sys_cfg.get("authType", "oauth2")
        ).lower()
        username = (
            self._runtime_override.get("username")
            or os.getenv("EMARSYS_USERNAME")
            or sys_cfg.get("username", "")
        )
        secret = (
            self._runtime_override.get("secret")
            or os.getenv("EMARSYS_SECRET")
            or sys_cfg.get("secret", "")
        )

        available_systems = [
            {
                "name": name,
                "label": s.get("label", name),
                "tenantId": s.get("tenantId", ""),
                "authType": s.get("authType", "oauth2"),
                "baseUrl": s.get("baseUrl", "https://api.emarsys.net"),
                "active": name == active_name,
            }
            for name, s in systems.items()
        ]

        return {
            "activeName": active_name,
            "label": sys_cfg.get("label", active_name),
            "tenantId": tenant_id,
            "authType": auth_type,
            "clientId": client_id,
            "clientSecret": client_secret,
            "baseUrl": base_url,
            "tokenUrl": token_url,
            "username": username,
            "secret": secret,
            "availableSystems": available_systems,
        }


# Global singleton
config = EmarsysConfig()
