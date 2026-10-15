"""catalog.py — Comprehensive Emarsys API catalog parsed from EmarsysV2PostmanCollection.json.

Exposes all 140 API endpoints with metadata, path parameter schemas, query parameter schemas,
sample payloads, and provides discovery + dynamic execution capabilities.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union

from client import emarsys_client

logger = logging.getLogger("emarsys.catalog")
BASE_DIR = Path(__file__).resolve().parent
POSTMAN_FILE = BASE_DIR / "EmarsysV2PostmanCollection.json"


def slugify(text: str) -> str:
    text = re.sub(r"[\s\-_\/:]+", "_", text.strip())
    text = re.sub(r"[^a-zA-Z0-9_]", "", text)
    return text.lower().strip("_")


class EndpointInfo:
    def __init__(
        self,
        operation_id: str,
        name: str,
        category: str,
        method: str,
        raw_url: str,
        path_template: str,
        path_variables: List[Dict[str, Any]],
        query_parameters: List[Dict[str, Any]],
        sample_body: Optional[Union[Dict[str, Any], str]],
        description: str,
    ):
        self.operation_id = operation_id
        self.name = name
        self.category = category
        self.method = method.upper()
        self.raw_url = raw_url
        self.path_template = path_template
        self.path_variables = path_variables
        self.query_parameters = query_parameters
        self.sample_body = sample_body
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "name": self.name,
            "category": self.category,
            "method": self.method,
            "raw_url": self.raw_url,
            "path_template": self.path_template,
            "path_variables": self.path_variables,
            "query_parameters": self.query_parameters,
            "sample_body": self.sample_body,
            "description": self.description,
        }


class EndpointCatalog:
    def __init__(self):
        self._endpoints: List[EndpointInfo] = []
        self._by_op_id: Dict[str, EndpointInfo] = {}
        self._by_category: Dict[str, List[EndpointInfo]] = {}
        self._load_collection()

    def _load_collection(self):
        if not POSTMAN_FILE.exists():
            logger.warning("Postman collection file not found at %s", POSTMAN_FILE)
            return

        try:
            with open(POSTMAN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to read %s: %s", POSTMAN_FILE, e)
            return

        def parse_items(items, category_path=""):
            for item in items:
                name = item.get("name", "unnamed")
                cur_cat = f"{category_path} > {name}" if category_path else name
                if "item" in item:
                    parse_items(item["item"], cur_cat)
                elif "request" in item:
                    req = item["request"]
                    method = req.get("method", "GET")
                    url_obj = req.get("url", {})
                    raw_url = url_obj.get("raw", "") if isinstance(url_obj, dict) else str(url_obj)

                    # Extract path variables
                    path_vars = []
                    if isinstance(url_obj, dict) and "variable" in url_obj:
                        for v in url_obj["variable"]:
                            path_vars.append({
                                "key": v.get("key"),
                                "value": v.get("value"),
                                "description": v.get("description", ""),
                            })

                    # Also find :var in raw url
                    for match in re.findall(r":([a-zA-Z0-9_]+)", raw_url):
                        if not any(pv["key"] == match for pv in path_vars):
                            path_vars.append({"key": match, "description": f"Path variable {match}"})

                    # Extract query parameters
                    query_params = []
                    if isinstance(url_obj, dict) and "query" in url_obj:
                        for q in url_obj["query"]:
                            query_params.append({
                                "key": q.get("key"),
                                "value": q.get("value"),
                                "description": q.get("description", ""),
                            })

                    # Extract body
                    sample_body = None
                    body_obj = req.get("body", {})
                    if body_obj.get("mode") == "raw" and body_obj.get("raw"):
                        raw_content = body_obj["raw"]
                        try:
                            sample_body = json.loads(raw_content)
                        except Exception:
                            sample_body = raw_content

                    op_id = slugify(name)
                    # Deduplicate op_id if collision occurs
                    original_op_id = op_id
                    counter = 2
                    while op_id in self._by_op_id:
                        op_id = f"{original_op_id}_{counter}"
                        counter += 1

                    # Simplify category
                    cat_parts = [p.strip() for p in cur_cat.split(">") if p.strip()]
                    clean_cat = cat_parts[1] if len(cat_parts) > 1 else (cat_parts[0] if cat_parts else "General")

                    info = EndpointInfo(
                        operation_id=op_id,
                        name=name,
                        category=clean_cat,
                        method=method,
                        raw_url=raw_url,
                        path_template=raw_url.replace("{{baseUrl}}", "").split("?")[0],
                        path_variables=path_vars,
                        query_parameters=query_params,
                        sample_body=sample_body,
                        description=req.get("description", "") or item.get("description", ""),
                    )
                    self._endpoints.append(info)
                    self._by_op_id[op_id] = info
                    self._by_category.setdefault(clean_cat, []).append(info)

        parse_items(data.get("item", []))
        logger.info("Loaded %d endpoints across %d categories from Postman collection", len(self._endpoints), len(self._by_category))

    def list_endpoints(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search and list endpoints in the catalog."""
        results = self._endpoints

        if category:
            cat_lower = category.lower()
            results = [e for e in results if cat_lower in e.category.lower()]

        if search:
            q = search.lower()
            results = [
                e for e in results
                if q in e.operation_id.lower()
                or q in e.name.lower()
                or q in e.category.lower()
                or q in e.method.lower()
                or q in e.raw_url.lower()
                or q in e.description.lower()
            ]

        total = len(results)
        paged = results[offset : offset + limit]

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "categories": sorted(list(self._by_category.keys())),
            "endpoints": [e.to_dict() for e in paged],
        }

    def get_endpoint(self, operation_id_or_name: str) -> Optional[EndpointInfo]:
        """Find an endpoint by operation ID or exact name."""
        s = operation_id_or_name.strip()
        if s in self._by_op_id:
            return self._by_op_id[s]
        slug = slugify(s)
        if slug in self._by_op_id:
            return self._by_op_id[slug]
        for e in self._endpoints:
            if e.name.lower() == s.lower():
                return e
        return None

    async def execute(
        self,
        operation_id_or_path: str,
        *,
        method: Optional[str] = None,
        path_params: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Execute any API endpoint by operation ID or raw path template."""
        ep = self.get_endpoint(operation_id_or_path)

        if ep:
            req_method = method or ep.method
            req_path = ep.path_template
        else:
            req_method = method or "GET"
            req_path = operation_id_or_path

        return await emarsys_client.request(
            method=req_method,
            path_or_url=req_path,
            path_params=path_params,
            params=query_params,
            json_data=body,
            timeout=timeout,
        )


# Global catalog instance
catalog = EndpointCatalog()
