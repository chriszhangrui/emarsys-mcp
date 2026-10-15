"""test_emarsys.py — Comprehensive test suite for SAP Emarsys FastMCP Server.

Includes:
1. Unit tests: configuration, token caching, field resolution, catalog indexing
2. Live integration tests against the Public_GDM_EMEA037 live tenant
"""

from __future__ import annotations

import asyncio
import json
import unittest

from catalog import catalog
from client import emarsys_client
from config import config
from field_manager import field_manager
import server


class TestEmarsysConfig(unittest.TestCase):
    def test_config_resolution(self):
        cfg = config.resolve()
        self.assertEqual(cfg["activeName"], "public_gdm_emea")
        self.assertEqual(cfg["tenantId"], "Public_GDM_EMEA037")
        self.assertTrue(cfg["clientId"].startswith("a0b92d9b"))
        self.assertEqual(cfg["authType"], "oauth2")


class TestFieldManager(unittest.TestCase):
    def test_standard_fields_resolution(self):
        self.assertEqual(field_manager.resolve_field_id("email"), "3")
        self.assertEqual(field_manager.resolve_field_id("first_name"), "1")
        self.assertEqual(field_manager.resolve_field_id("last_name"), "2")
        self.assertEqual(field_manager.resolve_field_id("3"), "3")

    def test_inbound_translation(self):
        inbound = {
            "email": "test@example.com",
            "first_name": "Jane",
            "last_name": "Doe",
            "city": "Walldorf",
        }
        translated = field_manager.translate_contact_inbound(inbound)
        self.assertEqual(translated["3"], "test@example.com")
        self.assertEqual(translated["1"], "Jane")
        self.assertEqual(translated["2"], "Doe")
        self.assertEqual(translated["11"], "Walldorf")

    def test_outbound_translation(self):
        raw = {
            "id": "12345",
            "3": "test@example.com",
            "1": "Jane",
            "2": "Doe",
        }
        translated = field_manager.translate_contact_outbound(raw)
        self.assertEqual(translated["id"], "12345")
        self.assertEqual(translated["email"], "test@example.com")
        self.assertEqual(translated["first_name"], "Jane")
        self.assertEqual(translated["last_name"], "Doe")


class TestEndpointCatalog(unittest.TestCase):
    def test_catalog_endpoint_count(self):
        self.assertEqual(len(catalog._endpoints), 140)

    def test_get_endpoint_by_slug(self):
        ep = catalog.get_endpoint("list_contact_lists")
        self.assertIsNotNone(ep)
        self.assertEqual(ep.method, "GET")
        self.assertIn("contactlist", ep.path_template)

    def test_search_endpoints(self):
        res = catalog.list_endpoints(search="email")
        self.assertGreater(res["total"], 0)
        self.assertTrue(any("email" in e["operation_id"] for e in res["endpoints"]))


class TestLiveIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_live_system_info(self):
        raw_res = await server.emarsys_system_info()
        data = json.loads(raw_res)
        self.assertEqual(data.get("status"), "connected")
        self.assertEqual(data.get("tenantId"), "Public_GDM_EMEA037")
        self.assertTrue(data.get("tokenStatus", {}).get("valid"))
        self.assertTrue(data.get("apiPing", {}).get("ok"))
        self.assertEqual(data.get("apiPing", {}).get("settingsPreview", {}).get("name"), "Public_GDM_EMEA")

    async def test_live_fields(self):
        raw_res = await server.emarsys_list_fields(search="email", limit=5)
        data = json.loads(raw_res)
        self.assertGreater(data.get("total_matched", 0), 0)
        self.assertTrue(any("email" in f.get("string_id", "").lower() for f in data["fields"]))

    async def test_live_field_choices(self):
        # Field 5 is Gender
        raw_res = await server.emarsys_get_field_choices(field_name_or_id="gender")
        data = json.loads(raw_res)
        self.assertEqual(data.get("field_id"), "5")
        self.assertIsInstance(data.get("choices"), list)
        self.assertGreater(len(data.get("choices")), 0)

    async def test_live_contact_lists(self):
        raw_res = await server.emarsys_list_contact_lists()
        data = json.loads(raw_res)
        self.assertGreater(data.get("total", 0), 0)
        first_list = data["lists"][0]
        self.assertIn("id", first_list)

        # Test single list details
        list_details = await server.emarsys_get_contact_list(first_list["id"], sample_limit=5)
        detail_data = json.loads(list_details)
        self.assertEqual(detail_data.get("list_id"), str(first_list["id"]))
        self.assertIn("total_contacts", detail_data)

    async def test_live_query_contact(self):
        # Query contact 860975315 known to exist in demo tenant
        raw_res = await server.emarsys_query_contact(contact_id="860975315")
        data = json.loads(raw_res)
        self.assertTrue(data.get("found"))
        self.assertGreater(data.get("count"), 0)
        contact = data["contacts"][0]
        self.assertIn("email", contact)
        self.assertIn("first_name", contact)

    async def test_live_list_segments(self):
        raw_res = await server.emarsys_list_segments(limit=5)
        data = json.loads(raw_res)
        self.assertGreater(data.get("total_matched", 0), 0)
        self.assertLessEqual(len(data.get("segments", [])), 5)

    async def test_live_list_email_campaigns(self):
        raw_res = await server.emarsys_list_email_campaigns(fromdate="2026-09-01", limit=5)
        data = json.loads(raw_res)
        self.assertIn("campaigns", data)
        self.assertIsInstance(data["campaigns"], list)

    async def test_live_list_programs(self):
        raw_res = await server.emarsys_list_programs(limit=5)
        data = json.loads(raw_res)
        self.assertGreater(data.get("total", 0), 0)
        self.assertLessEqual(len(data.get("programs", [])), 5)

    async def test_live_list_events(self):
        raw_res = await server.emarsys_list_events(limit=5)
        data = json.loads(raw_res)
        self.assertGreater(data.get("total", 0), 0)
        self.assertLessEqual(len(data.get("events", [])), 5)

    async def test_live_catalog_search_and_universal_invoker(self):
        # 1. Search endpoints
        search_res = await server.emarsys_search_api_endpoints(query="settings")
        s_data = json.loads(search_res)
        self.assertGreater(s_data.get("total", 0), 0)

        # 2. Universal API request to get_customer_settings
        exec_res = await server.emarsys_api_request(operation_id_or_path="get_customer_settings")
        e_data = json.loads(exec_res)
        self.assertEqual(e_data.get("replyCode"), 0)
        self.assertEqual(e_data.get("data", {}).get("name"), "Public_GDM_EMEA")

        # 3. Universal API request to list contact sources
        source_res = await server.emarsys_api_request(operation_id_or_path="list_contact_sources")
        src_data = json.loads(source_res)
        self.assertEqual(src_data.get("replyCode"), 0)
        self.assertIsInstance(src_data.get("data"), list)


if __name__ == "__main__":
    unittest.main()
