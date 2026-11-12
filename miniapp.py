"""Telegram Mini App API and static-site server."""
import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

import feed_core as fc
from storage import MAX_RECIPES

log = logging.getLogger("feedbot.miniapp")
BASE = Path(__file__).resolve().parent
STATIC = BASE / "miniapp_static"
MAX_QTY = 1_000_000
MAX_PRICE = 1_000_000_000
INIT_DATA_MAX_AGE = 86_400
FEED_USER = web.RequestKey("feed_user", dict)
FEED_USER_ID = web.RequestKey("feed_user_id", int)


def validate_init_data(init_data: str, bot_token: str, now: int | None = None) -> dict:
    """Verify Telegram's signed initData and return its user object."""
    if not init_data or len(init_data) > 16_384 or not bot_token:
        raise ValueError("Missing or invalid Telegram launch data")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("Malformed Telegram launch data") from exc
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("Duplicate Telegram launch-data field")
        fields[key] = value
    supplied_hash = fields.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", supplied_hash):
        raise ValueError("Missing Telegram signature")
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash.lower()):
        raise ValueError("Telegram signature is invalid")
    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
        uid = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Telegram launch data is incomplete") from exc
    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + 60 or current_time - auth_date > INIT_DATA_MAX_AGE:
        raise ValueError("Telegram launch data has expired")
    if uid <= 0 or not isinstance(user, dict):
        raise ValueError("Telegram user is invalid")
    return user


def _number(value, label: str, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise ValueError(f"{label} is outside the allowed range")
    return number


def _parse_rows(values, valid_rows, label: str, maximum: float) -> dict:
    if not isinstance(values, dict):
        raise ValueError(f"{label} must be an object")
    parsed = {}
    for raw_row, value in values.items():
        try:
            row = int(raw_row)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {label} item") from exc
        if row not in valid_rows or str(row) != str(raw_row) or row in parsed:
            raise ValueError(f"Invalid {label} item")
        parsed[row] = _number(value, label, maximum)
    return parsed


def parse_webapp_calculation(data: str, feeds) -> tuple[dict, dict, float | None]:
    if not isinstance(data, str) or len(data.encode("utf-8")) > 4096:
        raise ValueError("بيانات الخلطة أطول من المسموح.")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError("بيانات الخلطة غير صالحة.") from exc
    if not isinstance(payload, dict) or set(payload) != {"type", "qty", "prices", "target"}:
        raise ValueError("بيانات الخلطة غير مكتملة.")
    if payload["type"] != "feed_calculation":
        raise ValueError("نوع الطلب غير صالح.")
    valid_rows = {feed.row for feed in feeds}
    quantities = _parse_rows(payload["qty"], valid_rows, "quantity", MAX_QTY)
    prices = _parse_rows(payload["prices"], valid_rows, "price", MAX_PRICE)
    if set(quantities) != valid_rows or set(prices) != valid_rows:
        raise ValueError("بيانات بعض الخامات ناقصة.")
    raw_target = payload["target"]
    target = None if raw_target is None else _number(raw_target, "target", 100)
    return quantities, prices, target


async def _json_body(request):
    try:
        return await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest) as exc:
        raise ValueError("Invalid JSON request body") from exc


def _state(user, feeds) -> dict:
    quantities, prices = user["qty"], user["prices"]
    result = fc.compute(quantities, feeds, prices)
    lines = [{
        "row": feed.row,
        "name": feed.name_ar,
        "qty": qty,
        "share": share * 100,
        "protein": protein,
        "price": prices.get(feed.row, 0),
    } for feed, qty, protein, share in result["lines"]]
    target = user.get("target")
    advice = fc.advice(result, target, feeds)[1] if target else ""
    return {
        "feeds": [{
            "row": feed.row,
            "name": feed.name_ar,
            "protein_pct": feed.cp_as_fed * 100,
        } for feed in feeds],
        "qty": {str(row): value for row, value in quantities.items()},
        "prices": {str(row): value for row, value in prices.items()},
        "target": target,
        "recipes": list(user["recipes"]),
        "result": {
            "total_weight": result["total_weight"],
            "protein_kg": result["protein_kg"],
            "cp_pct": result["cp_pct"],
            "cost_per_ton": result["cost_per_ton"],
            "missing_price": [feed.name_ar for feed in result["missing_price"]],
            "lines": lines,
            "advice": advice,
        },
    }


def create_app(application, feeds, template_path: str, recalc: bool = False,
               require_subscription: bool = True, channel: str = "@KIRA349A",
               demo_init_data: str | None = None) -> web.Application:
    defaults = fc.default_quantities(feeds)
    valid_rows = {feed.row for feed in feeds}
    app = web.Application(client_max_size=1_048_576)

    @web.middleware
    async def authenticate(request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("tma "):
            return web.json_response({"error": "افتح التطبيق من تيليجرام أولاً."}, status=401)
        try:
            tg_user = validate_init_data(authorization[4:], application.bot.token)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=401)
        uid = int(tg_user["id"])
        store = application.bot_data["store"]
        user = store.touch(
            uid,
            " ".join(part for part in (tg_user.get("first_name"), tg_user.get("last_name")) if part),
            tg_user.get("username", ""),
            defaults,
            source="miniapp",
        )
        if user.get("banned"):
            return web.json_response({"error": "الحساب غير مسموح له باستخدام التطبيق."}, status=403)
        sub_cache = application.bot_data.setdefault("sub_cache", {})
        is_admin = uid in application.bot_data.get("admin_ids", [])
        if require_subscription and not is_admin and time.time() - sub_cache.get(uid, 0) >= 600:
            try:
                member = await application.bot.get_chat_member(channel, uid)
                subscribed = member.status in (
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                ) or (member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False))
                if not subscribed:
                    return web.json_response(
                        {"error": "اشترك في قناة البوت أولاً، ثم افتح التطبيق من جديد."},
                        status=403,
                    )
                application.bot_data["sub_cache"][uid] = time.time()
            except TelegramError:
                log.exception("Mini App subscription check failed for user %s", uid)
                if not application.bot_data.get("sub_warned") and application.bot_data.get("admin_ids"):
                    application.bot_data["sub_warned"] = True
                    try:
                        await application.bot.send_message(
                            application.bot_data["admin_ids"][0],
                            f"تعذّر التحقق من اشتراك Mini App في {channel}؛ تم السماح مؤقتاً.",
                        )
                    except TelegramError:
                        log.exception("Failed to notify admins about subscription-check failure")
        request[FEED_USER] = user
        request[FEED_USER_ID] = uid
        return await handler(request)

    app.middlewares.append(authenticate)

    async def get_state(request):
        return web.json_response(_state(request[FEED_USER], feeds))

    async def calculate(request):
        user = request[FEED_USER]
        if sum(user["qty"].values()) <= 0:
            return web.json_response({"error": "أضف خامة واحدة على الأقل قبل الحساب."}, status=400)
        store = application.bot_data["store"]
        store.count_calc(request[FEED_USER_ID])
        await store.flush(application.bot)
        return web.json_response(_state(user, feeds))

    async def update_state(request):
        try:
            payload = await _json_body(request)
            if not isinstance(payload, dict):
                raise ValueError("Invalid request")
            unknown = set(payload) - {"qty", "prices", "target", "scale_to", "reset"}
            if unknown:
                raise ValueError("Invalid request field")
            quantities = _parse_rows(payload["qty"], valid_rows, "quantity", MAX_QTY) if "qty" in payload else None
            prices = _parse_rows(payload["prices"], valid_rows, "price", MAX_PRICE) if "prices" in payload else None
            target = None
            if "target" in payload:
                raw_target = payload["target"]
                target = None if raw_target is None else _number(raw_target, "target", 100)
            if "reset" in payload and not isinstance(payload["reset"], bool):
                raise ValueError("Invalid reset value")
            user = request[FEED_USER]
            updated_qty = dict(defaults if payload.get("reset") else user["qty"])
            if quantities is not None:
                updated_qty.update(quantities)
            scale_to = None
            if "scale_to" in payload:
                scale_to = _number(payload["scale_to"], "scale", MAX_QTY * len(feeds))
                total = sum(updated_qty.values())
                if scale_to <= 0 or total <= 0:
                    raise ValueError("أدخل وزنًا إجماليًا أكبر من صفر.")
                scaled = fc.scale_quantities(updated_qty, scale_to)
                if any(value > MAX_QTY for value in scaled.values()):
                    raise ValueError("تجاوزت كمية خامة واحدة الحد الأقصى المسموح.")
                updated_qty = scaled
        except (ValueError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        if quantities is not None or scale_to is not None or payload.get("reset") is True:
            user["qty"] = updated_qty
        if prices is not None:
            user["prices"].update(prices)
        if "target" in payload:
            user["target"] = None if target in (None, 0) else target
        application.bot_data["store"].mark()
        await application.bot_data["store"].flush(application.bot)
        return web.json_response(_state(user, feeds))

    async def update_recipe(request):
        try:
            payload = await _json_body(request)
            if not isinstance(payload, dict):
                raise ValueError("Invalid request")
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
                raise ValueError("اكتب اسماً من 1 إلى 40 حرفاً.")
            name = name.strip()
            action = payload.get("action")
            user = request[FEED_USER]
            recipes = user["recipes"]
            if action == "save":
                if name not in recipes and len(recipes) >= MAX_RECIPES:
                    raise ValueError(f"الحد الأقصى {MAX_RECIPES} خلطة محفوظة.")
                recipes[name] = dict(user["qty"])
            elif action == "load":
                if name not in recipes:
                    return web.json_response({"error": "الخلطة غير موجودة."}, status=404)
                user["qty"] = {feed.row: recipes[name].get(feed.row, 0) for feed in feeds}
            elif action == "delete":
                if name not in recipes:
                    return web.json_response({"error": "الخلطة غير موجودة."}, status=404)
                del recipes[name]
            else:
                raise ValueError("إجراء غير صالح.")
        except (ValueError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        application.bot_data["store"].mark()
        await application.bot_data["store"].flush(application.bot)
        return web.json_response(_state(user, feeds))

    async def export_workbook(request):
        user = request[FEED_USER]
        if not any(value > 0 for value in user["qty"].values()):
            return web.json_response({"error": "أضف خامة واحدة على الأقل قبل تنزيل الملف."}, status=400)
        username = application.bot_data.get("username", "")
        footer = (
            (f"🤖 اتحسبت بواسطة بوت تيليجرام @{username} — t.me/{username}",
             f"https://t.me/{username}?start=xlsx")
            if username and os.environ.get("WATERMARK", "1") != "0" else None
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Feed_Calculation.xlsx")
            await asyncio.to_thread(
                fc.build_workbook,
                template_path,
                user["qty"],
                path,
                recalc,
                user["prices"],
                footer,
            )
            data = await asyncio.to_thread(Path(path).read_bytes)
        return web.Response(
            body=data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="Feed_Calculation.xlsx"'},
        )

    async def index(_request):
        if demo_init_data is not None:
            page = (STATIC / "index.html").read_text(encoding="utf-8")
            bootstrap = (
                "<script>window.__MINIAPP_DEMO_INIT_DATA__="
                + json.dumps(demo_init_data)
                + ";</script>"
            )
            return web.Response(
                text=page.replace("</head>", bootstrap + "</head>", 1),
                content_type="text/html",
            )
        return web.FileResponse(STATIC / "index.html")

    async def health(_request):
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/assets/{name}", _static_asset)
    app.router.add_get("/api/state", get_state)
    app.router.add_put("/api/state", update_state)
    app.router.add_post("/api/calculate", calculate)
    app.router.add_post("/api/recipes", update_recipe)
    app.router.add_post("/api/export", export_workbook)
    return app


async def _static_asset(request):
    name = request.match_info["name"]
    if name not in {"app.js", "style.css"}:
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name)


async def start_server(application, feeds, template_path: str, recalc: bool = False,
                       require_subscription: bool = True, channel: str = "@KIRA349A"):
    host = os.environ.get("WEBAPP_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBAPP_PORT", os.environ.get("PORT", "8080")))
    runner = web.AppRunner(create_app(application, feeds, template_path, recalc, require_subscription, channel))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    application.bot_data["miniapp_runner"] = runner
    log.info("Mini App web server listening on %s:%s", host, port)


async def stop_server(application):
    runner = application.bot_data.pop("miniapp_runner", None)
    if runner:
        await runner.cleanup()
