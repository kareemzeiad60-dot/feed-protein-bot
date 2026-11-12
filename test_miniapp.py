import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode

from aiohttp.test_utils import AioHTTPTestCase

import feed_core as fc
from miniapp import create_app, validate_init_data
from storage import Store


TOKEN = "123456:miniapp-test-token"


def signed_init_data(user_id=123, auth_date=None):
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": "tester"}),
    }
    data = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class MiniAppApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feeds = fc.load_feeds("Feed_Calculator_Bilingual.xlsx")
        self.store = Store(os.path.join(self.tmp.name, "data.json"), [])
        self.bot = SimpleNamespace(token=TOKEN)
        self.application = SimpleNamespace(bot=self.bot, bot_data={"store": self.store, "sub_cache": {}, "admin_ids": []})
        return create_app(
            self.application,
            self.feeds,
            "Feed_Calculator_Bilingual.xlsx",
            require_subscription=False,
        )

    async def tearDownAsync(self):
        await super().tearDownAsync()
        self.tmp.cleanup()

    def auth(self, init_data=None):
        return {"Authorization": f"tma {init_data or signed_init_data()}"}

    async def test_init_data_signature_and_expiry(self):
        now = int(time.time())
        self.assertEqual(validate_init_data(signed_init_data(auth_date=now), TOKEN, now)["id"], 123)
        with self.assertRaisesRegex(ValueError, "signature"):
            validate_init_data(signed_init_data().replace("test-query", "tampered"), TOKEN)
        with self.assertRaisesRegex(ValueError, "expired"):
            validate_init_data(signed_init_data(auth_date=now - 86_401), TOKEN, now)

    async def test_state_recipes_and_excel_export(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("حاسبة العليقة", await response.text())
        response = await self.client.get("/healthz")
        self.assertEqual((await response.json())["ok"], True)
        response = await self.client.get("/assets/app.js")
        self.assertEqual(response.status, 200)

        response = await self.client.get("/api/state", headers=self.auth())
        self.assertEqual(response.status, 200)
        state = await response.json()
        row = str(self.feeds[0].row)
        empty_qty = {str(feed.row): 0 for feed in self.feeds}

        response = await self.client.put(
            "/api/state",
            headers=self.auth(),
            json={"qty": {**empty_qty, row: 500}, "prices": {row: 12000}, "target": 18},
        )
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertEqual(state["result"]["total_weight"], 500)
        self.assertIsNotNone(state["result"]["cost_per_ton"])

        response = await self.client.post("/api/calculate", headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertEqual(self.store.users[123]["calcs"], 1)

        response = await self.client.post(
            "/api/recipes",
            headers=self.auth(),
            json={"action": "save", "name": "خلطة اختبار"},
        )
        self.assertEqual(response.status, 200)
        self.assertIn("خلطة اختبار", (await response.json())["recipes"])

        response = await self.client.put(
            "/api/state",
            headers=self.auth(),
            json={"qty": {**empty_qty, row: 10}},
        )
        self.assertEqual((await response.json())["result"]["total_weight"], 10)
        response = await self.client.post(
            "/api/recipes",
            headers=self.auth(),
            json={"action": "load", "name": "خلطة اختبار"},
        )
        self.assertEqual((await response.json())["result"]["total_weight"], 500)

        response = await self.client.post("/api/export", headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.read()).startswith(b"PK"))

        response = await self.client.post(
            "/api/recipes",
            headers=self.auth(),
            json={"action": "delete", "name": "خلطة اختبار"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["recipes"], [])

    async def test_local_preview_bootstrap_is_opt_in(self):
        app = create_app(
            self.application,
            self.feeds,
            "Feed_Calculator_Bilingual.xlsx",
            require_subscription=False,
            demo_init_data="local-signed-demo-data",
        )
        from aiohttp.test_utils import TestClient, TestServer

        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/")
            self.assertIn(
                "window.__MINIAPP_DEMO_INIT_DATA__=\"local-signed-demo-data\"",
                await response.text(),
            )
        finally:
            await client.close()

    async def test_rejects_unauthenticated_and_invalid_mutations(self):
        response = await self.client.get("/api/state")
        self.assertEqual(response.status, 401)

        headers = self.auth(signed_init_data().replace("test-query", "tampered"))
        response = await self.client.get("/api/state", headers=headers)
        self.assertEqual(response.status, 401)

        row = str(self.feeds[0].row)
        baseline = sum(fc.default_quantities(self.feeds).values())
        response = await self.client.put(
            "/api/state",
            headers=self.auth(),
            json={"qty": {row: 100}, "scale_to": 0},
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get("/api/state", headers=self.auth())
        self.assertEqual((await response.json())["result"]["total_weight"], baseline)


if __name__ == "__main__":
    unittest.main()
