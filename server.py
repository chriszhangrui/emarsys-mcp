#!/usr/bin/env python3
"""server.py — Python FastMCP Server for SAP Emarsys Customer Engagement Platform.

Built with FastMCP, fully supporting:
- Complete 140-endpoint catalog from the official Emarsys Postman Collection
- OAuth 2.0 Client Credentials Grant & legacy WSSE authentication
- Dynamic tenant switching and runtime credential configuration
- Smart bi-directional field name translation (email, first_name -> numeric IDs)
- Rate-limit backoff, retry resilience, and structured error responses
- Stdio mode (for Claude Code, Claude Desktop, Cursor) and HTTP mode (for SAP BTP / Joule)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP

from catalog import catalog
from client import EmarsysAPIError, emarsys_client
from config import config
from field_manager import field_manager

logger = logging.getLogger("emarsys.server")

SERVER_INSTRUCTIONS = """
SAP Emarsys Customer Engagement Platform MCP Server.

Capabilities:
1. System & Authentication:
   - Use `emarsys_system_info` to check tenant connectivity, OAuth2 token validity, and account info.
   - Use `emarsys_switch_system` or `emarsys_configure` to switch tenants or update credentials dynamically.
2. Contacts & Segments:
   - Use `emarsys_query_contact` to look up contact data by email or ID with human-friendly field names.
   - Use `emarsys_create_or_update_contact` to upsert contacts.
   - Use `emarsys_list_contact_lists` and `emarsys_get_contact_list` to manage static contact lists.
   - Use `emarsys_list_segments` and `emarsys_count_segment_contacts` to inspect dynamic segments.
3. Fields & Metadata:
   - Use `emarsys_list_fields` to search through available standard and custom contact fields.
   - Use `emarsys_get_field_choices` to view single-choice/multi-choice dropdown options.
4. Campaigns & Automation:
   - Use `emarsys_list_email_campaigns` and `emarsys_get_email_campaign` for email marketing.
   - Use `emarsys_list_programs` for Automation Center (AC) programs.
   - Use `emarsys_list_events` and `emarsys_trigger_event` to trigger marketing automations.
5. Complete 140-Endpoint API Catalog:
   - Use `emarsys_list_api_endpoints` or `emarsys_search_api_endpoints` to discover any of the 140 Postman endpoints.
   - Use `emarsys_api_request` to execute ANY endpoint in the collection by its operation_id or path template.
"""

mcp = FastMCP(
    name="sap-emarsys-mcp",
    instructions=SERVER_INSTRUCTIONS,
    version="1.0.0",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. System, Tenant & Diagnostic Tools
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_system_info",
    description="Check the current SAP Emarsys tenant connection, verify OAuth2 Bearer token validity, show expiration countdown, and ping the live API to retrieve account metadata and contact totals.",
)
async def emarsys_system_info() -> str:
    """Inspect active system connection, credentials status, and probe OAuth token."""
    try:
        diag = await emarsys_client.get_system_diagnostic()
        return json.dumps(diag, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_switch_system",
    description="Dynamically switch the active SAP Emarsys tenant in systems.json without restarting the server.",
)
async def emarsys_switch_system(system_name: str) -> str:
    """Switch active tenant in systems.json dynamically."""
    try:
        res = config.switch_system(system_name)
        diag = await emarsys_client.get_system_diagnostic()
        return json.dumps({"switch_result": res, "diagnostic": diag}, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_configure",
    description="Dynamically configure or override SAP Emarsys API credentials for the current session.",
)
async def emarsys_configure(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    base_url: Optional[str] = None,
    token_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    auth_type: Optional[str] = None,
    username: Optional[str] = None,
    secret: Optional[str] = None,
) -> str:
    """Configure or override Emarsys connection parameters at runtime."""
    try:
        config.set_runtime_override(
            clientId=client_id,
            clientSecret=client_secret,
            baseUrl=base_url,
            tokenUrl=token_url,
            tenantId=tenant_id,
            authType=auth_type,
            username=username,
            secret=secret,
        )
        diag = await emarsys_client.get_system_diagnostic()
        return json.dumps({"status": "configured", "diagnostic": diag}, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Contact & Contact List Tools (with Smart Field Name Translation)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_query_contact",
    description="Look up contact details in Emarsys by email or contact ID. Automatically translates cryptic numeric field IDs into human-readable field names (e.g. first_name, last_name, email).",
)
async def emarsys_query_contact(
    email: Optional[str] = None,
    contact_id: Optional[Union[str, int]] = None,
    fields: Optional[List[str]] = None,
) -> str:
    """Query contact data by email or contact ID."""
    try:
        await field_manager.ensure_loaded()

        # Determine keyId and keyValues
        if email:
            key_id = "3"  # Field 3 is Email in standard Emarsys schema
            key_values = [email]
        elif contact_id:
            key_id = "id"
            key_values = [str(contact_id)]
        else:
            return json.dumps({"error": "Either 'email' or 'contact_id' must be specified."}, indent=2)

        # Default fields to return if not specified: standard contact profile
        if not fields:
            fields = ["1", "2", "3", "4", "5", "11", "14", "15", "17"]  # first_name, last_name, email, birthdate, gender, city, country, phone, company
        else:
            # Translate friendly field names to numeric IDs
            fields = [field_manager.resolve_field_id(f) for f in fields]

        payload = {
            "keyId": key_id,
            "keyValues": key_values,
            "fields": fields,
        }

        res = await emarsys_client.request("POST", "/api/v3/contact/getdata", json_data=payload)
        data = res.get("data", {})
        results = data.get("result", [])

        # Translate output numeric keys to friendly names
        friendly_results = [field_manager.translate_contact_outbound(c) for c in results]

        return json.dumps(
            {
                "found": len(friendly_results) > 0,
                "count": len(friendly_results),
                "contacts": friendly_results,
                "errors": data.get("errors", []),
            },
            indent=2,
            ensure_ascii=False,
        )
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_create_or_update_contact",
    description="Create or update a contact in Emarsys. Accepts friendly field names like 'email', 'first_name', 'last_name', 'phone', 'city', 'company', etc. Updates if contact already exists.",
)
async def emarsys_create_or_update_contact(
    email: str,
    attributes: Dict[str, Any],
    contact_list_id: Optional[int] = None,
) -> str:
    """Create or update a single contact in Emarsys using friendly attributes."""
    try:
        await field_manager.ensure_loaded()

        contact_dict = dict(attributes)
        contact_dict["email"] = email

        # Convert friendly keys to Emarsys field IDs
        converted_contact = field_manager.translate_contact_inbound(contact_dict)

        payload: Dict[str, Any] = {
            "key_id": "3",  # Key by email
            "contacts": [converted_contact],
        }
        if contact_list_id:
            payload["contact_list_id"] = contact_list_id

        # Emarsys PUT /contact/?create_if_not_exists=1 upserts contacts
        res = await emarsys_client.request(
            "PUT",
            "/api/v3/contact",
            params={"create_if_not_exists": 1},
            json_data=payload,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_list_contact_lists",
    description="List all static contact lists in the Emarsys account, including their list ID, name, and creation date.",
)
async def emarsys_list_contact_lists() -> str:
    """List all contact lists in the tenant."""
    try:
        res = await emarsys_client.request("GET", "/api/v3/contactlist")
        lists = res.get("data", [])
        return json.dumps({"total": len(lists), "lists": lists}, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_get_contact_list",
    description="Get details of a specific contact list by ID, including total contact count and a sample of member contact IDs.",
)
async def emarsys_get_contact_list(
    list_id: Union[str, int],
    sample_limit: int = 10,
) -> str:
    """Get count and sample contact IDs in a contact list."""
    try:
        lid = str(list_id).strip()
        # 1. Get count
        count_res = await emarsys_client.request("GET", f"/api/v3/contactlist/{lid}/count")
        total_count = count_res.get("data", 0)

        # 2. Get member contact IDs
        member_res = await emarsys_client.request("GET", f"/api/v3/contactlist/{lid}")
        members = member_res.get("data", [])
        if not isinstance(members, list):
            members = []

        sample_ids = members[:sample_limit]

        return json.dumps(
            {
                "list_id": lid,
                "total_contacts": total_count,
                "sample_contact_ids": sample_ids,
                "returned_samples": len(sample_ids),
            },
            indent=2,
            ensure_ascii=False,
        )
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_create_contact_list",
    description="Create a new static contact list in Emarsys.",
)
async def emarsys_create_contact_list(
    name: str,
    description: Optional[str] = None,
) -> str:
    """Create a new contact list."""
    try:
        payload = {"name": name}
        if description:
            payload["description"] = description
        res = await emarsys_client.request("POST", "/api/v3/contactlist", json_data=payload)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_add_contacts_to_list",
    description="Add contacts to an existing contact list by their email addresses or contact IDs.",
)
async def emarsys_add_contacts_to_list(
    list_id: Union[str, int],
    emails: Optional[List[str]] = None,
    contact_ids: Optional[List[Union[str, int]]] = None,
) -> str:
    """Add contacts to a static contact list."""
    try:
        lid = str(list_id).strip()
        if emails:
            key_id = "3"
            external_ids = emails
        elif contact_ids:
            key_id = "id"
            external_ids = [str(c) for c in contact_ids]
        else:
            return json.dumps({"error": "Either 'emails' or 'contact_ids' must be provided."}, indent=2)

        payload = {
            "key_id": key_id,
            "external_ids": external_ids,
        }
        res = await emarsys_client.request("POST", f"/api/v3/contactlist/{lid}/add", json_data=payload)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Field & Schema Metadata Tools
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_list_fields",
    description="List contact fields in the Emarsys account with their numeric IDs, names, string_ids, and data types (shorttext, singlechoice, birthdate, etc.). Supports keyword filtering.",
)
async def emarsys_list_fields(
    search: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List contact fields with optional keyword search."""
    try:
        fields = await field_manager.list_all_fields(search=search)
        total = len(fields)
        return json.dumps(
            {
                "total_matched": total,
                "limit": limit,
                "fields": fields[:limit],
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_get_field_choices",
    description="List all choices/options available for a single-choice or multi-choice contact field (e.g., gender, country, salutation).",
)
async def emarsys_get_field_choices(
    field_name_or_id: Union[str, int],
    language: str = "en",
) -> str:
    """List available choices for a single-choice field."""
    try:
        await field_manager.ensure_loaded()
        fid = field_manager.resolve_field_id(field_name_or_id)
        res = await emarsys_client.request(
            "GET",
            f"/api/v3/field/{fid}/choice/translate/{language}",
        )
        return json.dumps(
            {
                "field_id": fid,
                "field_info": field_manager.get_field_info(fid),
                "choices": res.get("data", []),
            },
            indent=2,
            ensure_ascii=False,
        )
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Segments & Dynamic Filter Tools
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_list_segments",
    description="List dynamic segments (filters) in the Emarsys account. Supports keyword search and pagination (useful because large accounts have thousands of segments).",
)
async def emarsys_list_segments(
    search: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
) -> str:
    """List dynamic segments with optional filtering and pagination."""
    try:
        # GET /api/v3/filter
        res = await emarsys_client.request("GET", "/api/v3/filter", timeout=45.0)
        filters = res.get("data", [])
        if not isinstance(filters, list):
            filters = []

        if search:
            q = search.lower()
            filters = [f for f in filters if q in str(f.get("name", "")).lower() or q in str(f.get("id", ""))]

        total = len(filters)
        paged = filters[offset : offset + limit]

        return json.dumps(
            {
                "total_matched": total,
                "offset": offset,
                "limit": limit,
                "segments": paged,
            },
            indent=2,
            ensure_ascii=False,
        )
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_count_segment_contacts",
    description="Count how many contacts currently match a specific dynamic segment (filter) by its segment ID.",
)
async def emarsys_count_segment_contacts(segment_id: Union[str, int]) -> str:
    """Count contacts in a segment."""
    try:
        sid = str(segment_id).strip()
        res = await emarsys_client.request("GET", f"/api/v3/filter/{sid}/contacts/count")
        return json.dumps({"segment_id": sid, "count": res.get("data")}, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_create_segment",
    description="Create a new dynamic segment (filter) in Emarsys based on contact criteria and behavior rules. In Emarsys, segments are officially called 'filters'.",
)
async def emarsys_create_segment(
    name: str,
    contact_criteria: Dict[str, Any],
    description: Optional[str] = None,
    base_contact_list_id: Optional[Union[str, int]] = None,
) -> str:
    """Create a new dynamic segment (filter) in Emarsys."""
    try:
        payload: Dict[str, Any] = {
            "name": name,
            "contactCriteria": contact_criteria,
        }
        if description:
            payload["description"] = description
        if base_contact_list_id:
            payload["baseContactListId"] = str(base_contact_list_id)

        # Emarsys API: PUT /v2/filter (maps to /api/v3/filter under OAuth2)
        res = await emarsys_client.request("PUT", "/api/v3/filter", json_data=payload)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)



# ═══════════════════════════════════════════════════════════════════════════════
# 5. Email Marketing Campaign Tools
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_list_email_campaigns",
    description="List email campaigns in the Emarsys account with optional filtering by creation date (fromdate/todate: YYYY-MM-DD), launch status (0=not launched, 1=launched), or status.",
)
async def emarsys_list_email_campaigns(
    fromdate: Optional[str] = None,
    todate: Optional[str] = None,
    launched: Optional[int] = None,
    status: Optional[int] = None,
    limit: int = 50,
) -> str:
    """List email campaigns."""
    try:
        params: Dict[str, Any] = {}
        if fromdate:
            params["fromdate"] = fromdate
        if todate:
            params["todate"] = todate
        if launched is not None:
            params["launched"] = launched
        if status is not None:
            params["status"] = status

        # GET /api/v3/email/
        res = await emarsys_client.request("GET", "/api/v3/email/", params=params, timeout=30.0)
        emails = res.get("data", [])
        if not isinstance(emails, list):
            emails = []

        return json.dumps(
            {
                "total": len(emails),
                "limit": limit,
                "campaigns": emails[:limit],
            },
            indent=2,
            ensure_ascii=False,
        )
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_get_email_campaign",
    description="Retrieve full details, settings, and launch configuration of an email campaign by campaign ID.",
)
async def emarsys_get_email_campaign(campaign_id: Union[str, int]) -> str:
    """Get single email campaign details."""
    try:
        cid = str(campaign_id).strip()
        res = await emarsys_client.request("GET", f"/api/v3/email/{cid}")
        return json.dumps(res.get("data", res), indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_send_test_email",
    description="Send a test preview of an email campaign to one or more recipient email addresses without launching the live campaign.",
)
async def emarsys_send_test_email(
    campaign_id: Union[str, int],
    recipient_emails: List[str],
) -> str:
    """Send test email preview for a campaign."""
    try:
        cid = str(campaign_id).strip()
        payload = {
            "recipientlist": ", ".join(recipient_emails),
        }
        res = await emarsys_client.request("POST", f"/api/v3/email/{cid}/sendtestmail", json_data=payload)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Automation Center (AC) & External Event Tools
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_list_programs",
    description="List all Automation Center (AC) programs configured in the Emarsys account, including program ID, name, and status (active, draft, paused).",
)
async def emarsys_list_programs(limit: int = 50) -> str:
    """List Automation Center programs."""
    try:
        res = await emarsys_client.request("GET", "/api/v3/ac/programs")
        programs = res.get("data", [])
        if not isinstance(programs, list):
            programs = []
        return json.dumps({"total": len(programs), "limit": limit, "programs": programs[:limit]}, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_list_events",
    description="List external events configured in Emarsys that can be used to trigger automation programs or transactional emails.",
)
async def emarsys_list_events(limit: int = 50) -> str:
    """List external events."""
    try:
        res = await emarsys_client.request("GET", "/api/v3/event")
        events = res.get("data", [])
        if not isinstance(events, list):
            events = []
        return json.dumps({"total": len(events), "limit": limit, "events": events[:limit]}, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_trigger_event",
    description="Trigger an external event for a contact in Emarsys (e.g. cart abandonment, purchase completed, voucher assigned) with optional event payload data.",
)
async def emarsys_trigger_event(
    event_id: Union[str, int],
    email: Optional[str] = None,
    contact_id: Optional[Union[str, int]] = None,
    event_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Trigger an external event for a contact."""
    try:
        eid = str(event_id).strip()
        if email:
            key_id = "3"
            external_id = email
        elif contact_id:
            key_id = "id"
            external_id = str(contact_id)
        else:
            return json.dumps({"error": "Either 'email' or 'contact_id' must be specified."}, indent=2)

        payload: Dict[str, Any] = {
            "key_id": key_id,
            "external_id": external_id,
        }
        if event_data:
            payload["data"] = event_data

        res = await emarsys_client.request("POST", f"/api/v3/event/{eid}/trigger", json_data=payload)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Complete 140-Endpoint API Catalog Discovery & Universal Invoker
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="emarsys_list_api_endpoints",
    description="Browse and list all 140 endpoints from the official Emarsys Postman collection. Filter by category (e.g. Contact, Email, Event, Filter, Form, Automation) or search term.",
)
async def emarsys_list_api_endpoints(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
) -> str:
    """List endpoints from catalog."""
    res = catalog.list_endpoints(category=category, search=search, limit=limit, offset=offset)
    return json.dumps(res, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_search_api_endpoints",
    description="Search all 140 Emarsys API endpoints using natural language or keywords to find operation IDs, required parameters, and sample request bodies.",
)
async def emarsys_search_api_endpoints(query: str) -> str:
    """Search endpoints across name, description, method, path, and category."""
    res = catalog.list_endpoints(search=query, limit=15)
    return json.dumps(res, indent=2, ensure_ascii=False)


@mcp.tool(
    name="emarsys_api_request",
    description="Universal API executor for ANY of the 140 Emarsys endpoints in the Postman collection. Pass the operation_id (e.g. 'get_customer_settings', 'create_contacts', 'list_external_events') or raw path template, along with path_params, query_params, or body.",
)
async def emarsys_api_request(
    operation_id_or_path: str,
    method: Optional[str] = None,
    path_params: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
) -> str:
    """Execute any endpoint in the 140-operation catalog."""
    try:
        res = await catalog.execute(
            operation_id_or_path=operation_id_or_path,
            method=method,
            path_params=path_params,
            query_params=query_params,
            body=body,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except EmarsysAPIError as e:
        return json.dumps(e.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. FastMCP Resources & Prompts
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.resource("emarsys://tenant/status")
async def resource_tenant_status() -> str:
    """Live status of current active tenant and OAuth2 token."""
    diag = await emarsys_client.get_system_diagnostic()
    return json.dumps(diag, indent=2, ensure_ascii=False)


@mcp.resource("emarsys://fields/all")
async def resource_fields_all() -> str:
    """Cached dictionary of all standard and custom contact fields in the tenant."""
    fields = await field_manager.list_all_fields()
    return json.dumps(fields, indent=2, ensure_ascii=False)


@mcp.resource("emarsys://catalog/categories")
async def resource_catalog_categories() -> str:
    """List of all 21 API categories available in the 140-endpoint catalog."""
    cats = list(catalog._by_category.keys())
    return json.dumps({"total_categories": len(cats), "categories": sorted(cats)}, indent=2, ensure_ascii=False)


@mcp.prompt(name="welcome-journey-setup")
def prompt_welcome_journey(customer_name: str, industry: str) -> str:
    """Interactive prompt blueprint for designing a multi-step welcome journey in SAP Emarsys."""
    return f"""You are an SAP Emarsys Solution Architect. Design an end-to-end Welcome Customer Journey for:
Customer / Brand: {customer_name}
Industry: {industry}

Step-by-step implementation plan using emarsys-mcp:
1. Field Schema: Check existing fields (`emarsys_list_fields`) and recommend custom fields needed (e.g. VIP status, loyalty points).
2. Segment / Contact List: Create a target list (`emarsys_create_contact_list`) for newly registered subscribers.
3. External Event Trigger: Identify or create external event (`emarsys_list_events`) to hook signup from web/app.
4. Welcome Campaign: Outline 3 nurture emails with timing, subject lines, dynamic personalization tokens, and CTA.
5. Automation Center Flow: Connect external event -> immediate welcome email -> 3-day wait -> incentive voucher email.
"""


@mcp.prompt(name="audit-tenant-health")
def prompt_tenant_audit() -> str:
    """Prompt template for auditing tenant database health, active automations, and campaigns."""
    return """You are an SAP Emarsys Deliverability & Architecture Specialist.
Perform a comprehensive audit of the active Emarsys tenant:
1. System & Connection: Run `emarsys_system_info` to verify environment, contact totals, and timezones.
2. Contact Lists: Review contact lists with `emarsys_list_contact_lists` and evaluate segmentation granularity.
3. Automation Programs: Inspect Automation Center programs (`emarsys_list_programs`) to find active vs inactive journeys.
4. Email Campaigns: List recent campaigns with `emarsys_list_email_campaigns` and evaluate launch cadence.
5. Provide actionable recommendations for list hygiene, deliverability protection, and automation opportunities.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CLI Entry Point & Server Execution
# ═══════════════════════════════════════════════════════════════════════════════


async def run_diagnostics() -> bool:
    print("=" * 70)
    print(" SAP Emarsys FastMCP Server — Diagnostic Check")
    print("=" * 70)
    try:
        diag = await emarsys_client.get_system_diagnostic()
        print(f"Status:             {diag.get('status').upper()}")
        print(f"Active System:      {diag.get('activeSystem')}")
        print(f"Tenant Label:       {diag.get('tenantLabel')}")
        print(f"Tenant ID:          {diag.get('tenantId')}")
        print(f"Base URL:           {diag.get('baseUrl')}")
        print(f"Auth Type:          {diag.get('authType')}")
        print(f"Token Valid:        {diag.get('tokenStatus', {}).get('valid')}")
        print(f"Token Expires In:   {diag.get('tokenStatus', {}).get('expiresInSeconds')}s")

        api_ping = diag.get("apiPing", {})
        print(f"API Ping OK:        {api_ping.get('ok')}")
        print(f"API Latency:        {api_ping.get('latencyMs')} ms")
        if api_ping.get("settingsPreview"):
            preview = api_ping["settingsPreview"]
            print(f"Account Name:       {preview.get('name')}")
            print(f"Environment:        {preview.get('environment')}")
            print(f"Total Contacts:     {preview.get('totalContacts')}")
            print(f"Timezone:           {preview.get('timezone')}")
        print(f"Catalog Endpoints:  {len(catalog._endpoints)} operations loaded")
        print("=" * 70)
        return bool(api_ping.get("ok"))
    except Exception as e:
        print(f"Diagnostic failed with error: {e}")
        print("=" * 70)
        return False


def main():
    parser = argparse.ArgumentParser(description="SAP Emarsys Customer Engagement FastMCP Server")
    parser.add_argument("--check", "--test", action="store_true", help="Test live connection and exit")
    parser.add_argument("--list-tools", action="store_true", help="List registered MCP tools and exit")
    parser.add_argument("--http", action="store_true", help="Run in HTTP / SSE mode instead of stdio")
    parser.add_argument("--transport", type=str, default="http", choices=["http", "sse", "streamable-http"], help="HTTP transport type: 'http' (/mcp) or 'sse' (/sse, /messages)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 8000)), help="Port for HTTP mode (default: 8000)")
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "0.0.0.0"), help="Host for HTTP mode (default: 0.0.0.0)")

    args = parser.parse_args()

    if args.check:
        ok = asyncio.run(run_diagnostics())
        sys.exit(0 if ok else 1)

    if args.list_tools:
        async def _print_tools():
            tools = await mcp.list_tools()
            print("Registered FastMCP Tools:")
            for t in tools:
                desc = (t.description or "").split(".")[0]
                print(f" - {t.name:32} : {desc}")
            print(f"\nTotal High-Level Tools: {len(tools)}")
            print(f"Catalog Endpoints Accessible via emarsys_api_request: {len(catalog._endpoints)}")

        asyncio.run(_print_tools())
        sys.exit(0)

    # Launch server
    if args.http or os.getenv("MCP_HTTP", "").lower() in ("true", "1", "yes"):
        transport = args.transport
        endpoint_hint = "/mcp" if transport in ("http", "streamable-http") else "/sse"
        print(f"🚀 Starting Emarsys FastMCP Server in HTTP mode ({transport}) on http://{args.host}:{args.port}{endpoint_hint}")
        mcp.run(transport=transport, host=args.host, port=args.port)
    else:
        # Default stdio
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
