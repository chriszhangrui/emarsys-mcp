# SAP Emarsys FastMCP Server (`emarsys-mcp`)

A production-grade Python [FastMCP](https://github.com/jlowin/fastmcp) server providing comprehensive, robust integration with the **SAP Emarsys Customer Engagement Platform**.

Pre-configured and live-verified against tenant **`Public_GDM_EMEA037`** (`public_gdm_emea`).

---

## 🌟 Key Highlights

- **Complete 140-Endpoint Official Postman Collection Coverage**: Directly parsed from the official Emarsys Postman collection. Every single API is cataloged and executable via `emarsys_api_request`.
- **22 High-Level Specialized MCP Tools**: Intuitive, strongly typed tools for everyday marketing operations across Contacts, Contact Lists, Fields, Segments/Filters, Email Campaigns, AC Programs, and External Events.
- **Bi-Directional Smart Field Translation**: Transparently maps friendly field names (`email`, `first_name`, `last_name`, `city`, `company`, etc.) to Emarsys internal numeric field IDs (`3`, `1`, `2`, `11`, `17`) for both inbound requests and outbound responses.
- **Enterprise-Grade Authentication**:
  - **Modern OAuth 2.0 / OIDC**: Client credentials grant using `client_secret_basic` against `https://auth.emarsys.net/oauth2/token` with automatic token caching and proactive renewal.
  - **Legacy WSSE Fallback**: Supports older `X-WSSE` `UsernameToken` authentication with Nonce and SHA-1 PasswordDigest.
- **Network Resilience & Robustness**: Automatic backoff retry on HTTP 429 (respecting `Retry-After`) and 5xx transient server errors, event loop isolation, structured error parsing (`replyCode`, `replyText`, `data.errors`).
- **Multi-Tenant Management**: Switch active accounts on the fly via `systems.json`, environment variables, or the `emarsys_switch_system` tool without restarting the server.
- **Dual Transport Modes**: Standard `stdio` (for Claude Code, Claude Desktop, Cursor) and `SSE/HTTP` (for SAP BTP / Joule).

---

## 🏗️ Architecture

```
┌────────────────────────────────────────────────────────┐
│              AI Agents / LLM Clients                   │
│   (Claude Code, Claude Desktop, Cursor, Antigravity)   │
└──────────────────────────┬─────────────────────────────┘
                           │ JSON-RPC (stdio / SSE)
┌──────────────────────────▼─────────────────────────────┐
│               FastMCP Server (server.py)               │
│                                                        │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │ 22 High-Level Tools  │    │ 140-Endpoint Catalog │  │
│  │ (Contacts, Emails,   │    │  (Universal Executor │  │
│  │  Lists, Automations) │    │  emarsys_api_request)│  │
│  └──────────┬───────────┘    └──────────┬───────────┘  │
│             │                           │              │
│  ┌──────────▼───────────────────────────▼───────────┐  │
│  │         Smart Field Manager (field_manager.py)   │  │
│  │   (email <-> 3, first_name <-> 1, city <-> 11)   │  │
│  └──────────────────────┬───────────────────────────┘  │
│                         │                              │
│  ┌──────────────────────▼───────────────────────────┐  │
│  │            HTTP Client & Auth (client.py)        │  │
│  │  • OAuth 2.0 Token Caching & Auto-Renewal        │  │
│  │  • Legacy WSSE SHA-1 Digest Fallback             │  │
│  │  • Exponential Backoff & 429 Rate-Limit Handling │  │
│  │  • Multi-Loop Async Isolation (httpx)            │  │
│  └──────────────────────┬───────────────────────────┘  │
└─────────────────────────┼──────────────────────────────┘
                          │ HTTPS (Bearer Token)
┌─────────────────────────▼──────────────────────────────┐
│           SAP Emarsys Cloud Platform                   │
│     • Auth Server: https://auth.emarsys.net            │
│     • API Gateway: https://api.emarsys.net/api/v3      │
│     • Active Tenant: Public_GDM_EMEA037                │
└────────────────────────────────────────────────────────┘
```

---

## 🛠️ Registered High-Level Tools (22 Tools)

### 1. System & Authentication
| Tool Name | Description |
| :--- | :--- |
| `emarsys_system_info` | Verify connectivity, OAuth token validity, expiration countdown, and retrieve account metadata and contact totals. |
| `emarsys_switch_system` | Dynamically switch the active tenant in `systems.json`. |
| `emarsys_configure` | Override credentials for the current session at runtime. |

### 2. Contacts & Contact Lists
| Tool Name | Description |
| :--- | :--- |
| `emarsys_query_contact` | Look up contact by email or ID with friendly field names. |
| `emarsys_create_or_update_contact` | Upsert contact using friendly attributes (e.g. `first_name`, `email`). |
| `emarsys_list_contact_lists` | List all static contact lists in the tenant with creation timestamps and IDs. |
| `emarsys_get_contact_list` | Get member contact IDs and total count for a contact list. |
| `emarsys_create_contact_list` | Create a new static contact list. |
| `emarsys_add_contacts_to_list` | Add contacts to an existing list by email addresses or contact IDs. |

### 3. Fields & Metadata
| Tool Name | Description |
| :--- | :--- |
| `emarsys_list_fields` | Search and list standard and custom contact fields (IDs, names, string_ids, types). |
| `emarsys_get_field_choices` | List dropdown choices for single-choice or multi-choice fields (e.g. gender, country). |

### 4. Segments & Filters
| Tool Name | Description |
| :--- | :--- |
| `emarsys_list_segments` | List dynamic segments with keyword search and pagination (handles 9,000+ segments smoothly). |
| `emarsys_count_segment_contacts` | Count contacts matching a dynamic segment. |

### 5. Email Marketing Campaigns
| Tool Name | Description |
| :--- | :--- |
| `emarsys_list_email_campaigns` | List email campaigns filtered by creation date (`fromdate`, `todate`), launch status, or status. |
| `emarsys_get_email_campaign` | Retrieve full campaign details and launch configuration. |
| `emarsys_send_test_email` | Send a preview/test of an email campaign to specified test recipients. |

### 6. Automation & Events
| Tool Name | Description |
| :--- | :--- |
| `emarsys_list_programs` | List Automation Center (AC) programs with status (`active`, `indesign`, `paused`). |
| `emarsys_list_events` | List external events available to trigger automations. |
| `emarsys_trigger_event` | Trigger an external event for a contact (e.g. abandoned cart, voucher issued). |

### 7. Universal 140-Endpoint API Catalog
| Tool Name | Description |
| :--- | :--- |
| `emarsys_list_api_endpoints` | Browse all 140 endpoints from the official Postman collection grouped by category. |
| `emarsys_search_api_endpoints` | Search endpoints across name, path, method, and description. |
| `emarsys_api_request` | **Universal executor**: Call ANY of the 140 endpoints by `operation_id` or path template. |

---

## ⚡ FastMCP Resources & Prompts

### Resources
- `emarsys://tenant/status`: Live diagnostic JSON of the active tenant, token health, and API latency.
- `emarsys://fields/all`: Cached schema dictionary of all contact fields.
- `emarsys://catalog/categories`: Catalog of all 21 functional API categories.

### Prompts
- `welcome-journey-setup`: Step-by-step Solution Architect blueprint for designing an onboarding journey.
- `audit-tenant-health`: Deliverability and hygiene audit of contact lists, segments, and automations.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+ (Python 3.12 recommended)
- `uv` package manager (or standard `venv`)

### 2. Virtual Environment Setup
```bash
cd /Users/I319510/.mcp-servers/emarsys-mcp

# Create virtual environment
uv venv --python 3.12 .venv

# Install dependencies
uv pip install --python .venv/bin/python -r requirements.txt
```

### 3. Diagnostic Health Check
Verify live connection against the `Public_GDM_EMEA037` tenant:
```bash
.venv/bin/python server.py --check
```
Output:
```text
======================================================================
 SAP Emarsys FastMCP Server — Diagnostic Check
======================================================================
Status:             CONNECTED
Active System:      public_gdm_emea
Tenant Label:       Public GDM EMEA (Public_GDM_EMEA037)
Tenant ID:          Public_GDM_EMEA037
Base URL:           https://api.emarsys.net
Auth Type:          oauth2
Token Valid:        True
Token Expires In:   3598s
API Ping OK:        True
API Latency:        1489.0 ms
Account Name:       Public_GDM_EMEA
Environment:        demo.emarsys.net
Total Contacts:     4807
Timezone:           Europe/Vienna
Catalog Endpoints:  140 operations loaded
======================================================================
```

### 4. Run Automated Test Suite
```bash
.venv/bin/python -m unittest test_emarsys.py -v
```
All 17 unit and live integration tests run against the tenant and pass with `OK`.

---

## 💻 Client Configuration

### Claude Desktop (`claude_desktop_config.json`)
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "emarsys-mcp": {
      "command": "/Users/I319510/.mcp-servers/emarsys-mcp/.venv/bin/python",
      "args": [
        "/Users/I319510/.mcp-servers/emarsys-mcp/server.py"
      ],
      "env": {
        "EMARSYS_ACTIVE_SYSTEM": "public_gdm_emea",
        "EMARSYS_CLIENT_ID": "a0b92d9b-f1db-40e5-9ac2-4b588fe8c0b4",
        "EMARSYS_CLIENT_SECRET": "qEbuF_vF9TOFsZ0MuwvH1zEjyi",
        "EMARSYS_BASE_URL": "https://api.emarsys.net",
        "EMARSYS_TOKEN_URL": "https://auth.emarsys.net/oauth2/token",
        "EMARSYS_TENANT_ID": "Public_GDM_EMEA037"
      }
    }
  }
}
```

### Claude Code (`~/.claude.json`)
```json
{
  "mcpServers": {
    "emarsys-mcp": {
      "command": "/Users/I319510/.mcp-servers/emarsys-mcp/.venv/bin/python",
      "args": ["/Users/I319510/.mcp-servers/emarsys-mcp/server.py"]
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "emarsys-mcp": {
      "command": "/Users/I319510/.mcp-servers/emarsys-mcp/.venv/bin/python",
      "args": ["/Users/I319510/.mcp-servers/emarsys-mcp/server.py"]
    }
  }
}
```

---

## ⚙️ Configuration Files

### `systems.json` (Multi-Tenant Profiles)
```json
{
  "active": "public_gdm_emea",
  "systems": {
    "public_gdm_emea": {
      "label": "Public GDM EMEA (Public_GDM_EMEA037)",
      "tenantId": "Public_GDM_EMEA037",
      "clientId": "a0b92d9b-f1db-40e5-9ac2-4b588fe8c0b4",
      "clientSecret": "qEbuF_vF9TOFsZ0MuwvH1zEjyi",
      "baseUrl": "https://api.emarsys.net",
      "tokenUrl": "https://auth.emarsys.net/oauth2/token",
      "jwksUrl": "https://auth.emarsys.net/.well-known/jwks.json",
      "authType": "oauth2"
    }
  }
}
```

---

## 🛡️ Robustness & Safety Guarantee

1. **Token Refresh Without Interruption**: Tokens are refreshed automatically 60 seconds before expiration. If concurrent requests occur during token renewal, double-checked locking ensures only one refresh call is made while other requests await the fresh token.
2. **Context Window Protection**: Large collections (like Segments with 9,000+ records) are paginated and searchable by default so LLM context windows are never flooded with megabytes of JSON.
3. **Safe Upserts**: Contact creations utilize `create_if_not_exists=1` to prevent duplicate record collisions.
4. **URL Rewrite Integrity**: Postman `/v2/` paths are seamlessly mapped to `/api/v3/` for modern Bearer tokens while preserving legacy routing for WSSE.

---

## 📄 License
Internal SAP Solution Engineering Asset.
