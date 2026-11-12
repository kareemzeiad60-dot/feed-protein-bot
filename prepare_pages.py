"""Build a self-contained, browser-only GitHub Pages preview of the Mini App."""
import argparse
import json
import shutil
from pathlib import Path

import feed_core as fc


ROOT = Path(__file__).resolve().parent


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "miniapp_static", output, dirs_exist_ok=True)
    feeds = fc.load_feeds(str(ROOT / "Feed_Calculator_Bilingual.xlsx"))
    payload = [{
        "row": feed.row,
        "name": feed.name_ar,
        "protein_pct": feed.cp_as_fed * 100,
        "default_qty": feed.default_qty,
    } for feed in feeds]
    (output / "demo-feeds.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    index = output / "index.html"
    html = index.read_text(encoding="utf-8")
    app_script = '  <script defer src="./app.js?v=components-only"></script>'
    demo_flag = '  <script>window.__MINIAPP_PAGES_DEMO__ = true;</script>\n'
    if html.count(app_script) != 1:
        raise ValueError("Mini App script tag was missing or duplicated")
    index.write_text(html.replace(app_script, demo_flag + app_script, 1), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    build(args.output)
