"""Run a local-only Mini App preview with isolated, temporary demo data."""
import hashlib
import hmac
import json
import os
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from aiohttp import web

import feed_core as fc
from miniapp import create_app
from storage import Store


DEMO_TOKEN = "local-miniapp-preview-token"
DEMO_USER_ID = 987654321


def demo_init_data() -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "local-preview",
        "user": json.dumps({"id": DEMO_USER_ID, "first_name": "زائر تجريبي"}, ensure_ascii=False),
    }
    data = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", DEMO_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(fields)


def main():
    if os.environ.get("WEBAPP_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Local preview must bind to localhost only.")

    feeds = fc.load_feeds(os.path.join(os.path.dirname(__file__), "Feed_Calculator_Bilingual.xlsx"))
    with tempfile.TemporaryDirectory(prefix="feed-miniapp-demo-") as data_dir:
        bot = SimpleNamespace(token=DEMO_TOKEN)
        application = SimpleNamespace(
            bot=bot,
            bot_data={
                "store": Store(os.path.join(data_dir, "demo-data.json"), []),
                "sub_cache": {},
                "admin_ids": [],
                "username": "local_demo",
            },
        )
        app = create_app(
            application,
            feeds,
            os.path.join(os.path.dirname(__file__), "Feed_Calculator_Bilingual.xlsx"),
            require_subscription=False,
            demo_init_data=demo_init_data(),
        )
        port = int(os.environ.get("WEBAPP_PORT", "8080"))
        print(f"Mini App local preview: http://127.0.0.1:{port}", flush=True)
        print("Demo data is temporary and will be removed when this process stops.", flush=True)
        web.run_app(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
