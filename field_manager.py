"""field_manager.py — Emarsys field dictionary and bi-directional name-to-id translation.

Emarsys contacts use numeric field IDs (e.g., 1=First Name, 2=Last Name, 3=Email).
This manager translates human-friendly and programmatic field names (e.g. "email", "first_name",
"city") to Emarsys numeric IDs and vice versa, allowing LLMs to interact with contacts naturally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union

from client import emarsys_client

logger = logging.getLogger("emarsys.fields")

# Standard Emarsys Core System Fields fallback
STANDARD_FIELDS = {
    "0": {"name": "Interests", "string_id": "interests", "type": "interests"},
    "1": {"name": "First Name", "string_id": "first_name", "type": "shorttext"},
    "2": {"name": "Last Name", "string_id": "last_name", "type": "shorttext"},
    "3": {"name": "Email", "string_id": "email", "type": "longtext"},
    "4": {"name": "Date of Birth", "string_id": "birthdate", "type": "birthdate"},
    "5": {"name": "Gender", "string_id": "gender", "type": "singlechoice"},
    "6": {"name": "Marital Status", "string_id": "marital_status", "type": "singlechoice"},
    "7": {"name": "Children", "string_id": "children", "type": "singlechoice"},
    "8": {"name": "Education", "string_id": "education", "type": "singlechoice"},
    "9": {"name": "Title", "string_id": "title", "type": "singlechoice"},
    "10": {"name": "Address", "string_id": "address", "type": "shorttext"},
    "11": {"name": "City", "string_id": "city", "type": "shorttext"},
    "12": {"name": "State", "string_id": "state", "type": "shorttext"},
    "13": {"name": "ZIP Code", "string_id": "zip", "type": "shorttext"},
    "14": {"name": "Country", "string_id": "country", "type": "singlechoice"},
    "15": {"name": "Phone", "string_id": "phone", "type": "shorttext"},
    "16": {"name": "Fax", "string_id": "fax", "type": "shorttext"},
    "17": {"name": "Company", "string_id": "company", "type": "shorttext"},
    "18": {"name": "Department", "string_id": "department", "type": "shorttext"},
    "19": {"name": "Industry", "string_id": "industry", "type": "singlechoice"},
    "20": {"name": "Position", "string_id": "position", "type": "shorttext"},
    "31": {"name": "Mobile", "string_id": "mobile", "type": "shorttext"},
    "35": {"name": "Opt-In", "string_id": "optin", "type": "singlechoice"},
}


class FieldManager:
    def __init__(self):
        self._fields_by_id: Dict[str, Dict[str, Any]] = dict(STANDARD_FIELDS)
        self._id_by_string_id: Dict[str, str] = {}
        self._id_by_name_lower: Dict[str, str] = {}
        self._last_loaded_at: float = 0.0
        self._ttl_seconds: float = 3600.0  # 1 hour cache
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop = None
        self._rebuild_indices()

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._lock_loop != loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _rebuild_indices(self):
        self._id_by_string_id.clear()
        self._id_by_name_lower.clear()
        for fid, finfo in self._fields_by_id.items():
            sid = finfo.get("string_id")
            if sid:
                self._id_by_string_id[sid.lower()] = fid
                # Also handle camelCase or snake_case variations
                self._id_by_string_id[sid.lower().replace("_", "")] = fid
            name = finfo.get("name")
            if name:
                self._id_by_name_lower[name.lower()] = fid
                self._id_by_name_lower[name.lower().replace(" ", "").replace("_", "")] = fid

    async def ensure_loaded(self, force_refresh: bool = False):
        """Fetch all fields from live API and build lookup index."""
        now = time.time()
        if not force_refresh and (now - self._last_loaded_at) < self._ttl_seconds:
            return

        async with self._get_lock():
            if not force_refresh and (now - self._last_loaded_at) < self._ttl_seconds:
                return

            try:
                res = await emarsys_client.request("GET", "/api/v3/field", timeout=15.0)
                data = res.get("data", [])
                if isinstance(data, list):
                    for f in data:
                        fid = str(f.get("id"))
                        self._fields_by_id[fid] = {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "string_id": f.get("string_id"),
                            "application_type": f.get("application_type"),
                        }
                    self._rebuild_indices()
                    self._last_loaded_at = time.time()
                    logger.info("Loaded %d fields into FieldManager cache", len(self._fields_by_id))
            except Exception as e:
                logger.warning("Failed to refresh fields from API, using cached/standard fields: %s", e)

    def resolve_field_id(self, key_or_name: Union[str, int]) -> str:
        """Resolve a field name, string_id, or numeric ID to Emarsys numeric string ID."""
        s = str(key_or_name).strip()
        # If already numeric
        if s.isdigit():
            return s

        s_lower = s.lower()
        if s_lower in self._id_by_string_id:
            return self._id_by_string_id[s_lower]

        if s_lower in self._id_by_name_lower:
            return self._id_by_name_lower[s_lower]

        cleaned = s_lower.replace("_", "").replace(" ", "").replace("-", "")
        if cleaned in self._id_by_string_id:
            return self._id_by_string_id[cleaned]
        if cleaned in self._id_by_name_lower:
            return self._id_by_name_lower[cleaned]

        return s

    def get_field_friendly_name(self, field_id: Union[str, int]) -> str:
        """Get best human-friendly identifier (string_id or name) for a numeric field ID."""
        fid = str(field_id).strip()
        info = self._fields_by_id.get(fid)
        if info:
            return info.get("string_id") or info.get("name") or fid
        return fid

    def get_field_info(self, field_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        fid = self.resolve_field_id(field_id)
        return self._fields_by_id.get(fid)

    async def list_all_fields(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all fields, optionally filtering by keyword."""
        await self.ensure_loaded()
        res = list(self._fields_by_id.values())
        if search:
            q = search.lower()
            res = [
                f for f in res
                if q in str(f.get("name", "")).lower()
                or q in str(f.get("string_id", "")).lower()
                or q in str(f.get("id", "")).lower()
                or q in str(f.get("application_type", "")).lower()
            ]
        return res

    def translate_contact_inbound(self, contact_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a user/LLM contact dict with friendly keys to Emarsys numeric field IDs."""
        result = {}
        for k, v in contact_dict.items():
            if k in ("id", "uid", "key_id"):
                result[k] = v
            else:
                fid = self.resolve_field_id(k)
                result[fid] = v
        return result

    def translate_contact_outbound(self, contact_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an Emarsys contact record with numeric keys to friendly names."""
        result = {}
        for k, v in contact_dict.items():
            if k in ("id", "uid"):
                result[k] = v
            elif k.isdigit():
                friendly = self.get_field_friendly_name(k)
                result[friendly] = v
                # Also preserve numeric id key if friendly differs
                if friendly != k:
                    result[f"field_{k}"] = v
            else:
                result[k] = v
        return result


# Global instance
field_manager = FieldManager()
