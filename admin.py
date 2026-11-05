"""
ميزات الأدمن: لوحة تحكم، إحصائيات، برودكاست (رسالة جماعية)، حظر/فك حظر، ملف المستخدمين.
الأدمن = أي رقم (ID) في متغير ADMIN_IDS. اعرف رقمك بإرسال /id للبوت.
"""
import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timezone

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import CommandHandler, ContextTypes

log = logging.getLogger("feedbot.admin")
SEND_DELAY = 0.05          # ~20 رسالة/ثانية (حد تيليجرام حوالي 30)


def is_admin(context, uid: int) -> bool:
    return uid in context.application.bot_data.get("admin_ids", [])


def _store(context):
    return context.application.bot_data["store"]


def _ts(t) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")


def panel_view(context):
    s = _store(context).stats()
    text = (
        "🛠 لوحة الأدمن\n\n"
        f"👥 إجمالي المستخدمين: {s['total']}\n"
        f"🆕 جداد آخر 24 ساعة: {s['new_24h']}\n"
        f"🟢 نشطين آخر 24 ساعة: {s['active_24h']}\n"
        f"📅 نشطين آخر 7 أيام: {s['active_7d']}\n"
        f"📥 مستوردين لسه ماكلّموش البوت: {s['imported_pending']}\n"
        f"🚫 عاملين بلوك للبوت: {s['blocked']}   |   ⛔ محظورين: {s['banned']}\n\n"
        f"🧮 عمليات الحساب: {s['calcs_total']} (النهاردة {s['calcs_today']})\n"
        f"🐄 عمليات تعرّف السلالة: {s['scans_total']}\n"
        f"⭐ تقييمات المستخدمين: ✅ {s['fb_ok']} • ❌ {s['fb_bad']}"
        + (f" • دقة تقريبية {100 * s['fb_ok'] / (s['fb_ok'] + s['fb_bad']):.0f}%" if s['fb_ok'] + s['fb_bad'] >= 5 else "") + "\n"
        f"📤 صور اتبعتت للتحسين: {s['fb_shared']}\n"
        f"📈 المصادر: " + (" • ".join(f"{k} {v}" for k, v in s["sources"]) or "—") + "\n"
        f"📣 هيوصلهم البرودكاست: {len(_store(context).broadcast_targets())}"
    )
    kb = Markup([
        [Btn("📣 رسالة جماعية (برودكاست)", callback_data="adm:bc")],
        [Btn("📥 استيراد مستخدمين", callback_data="adm:imp"), Btn("👥 ملف المستخدمين", callback_data="adm:csv")],
        [Btn("🔄 تحديث", callback_data="adm:panel")],
        [Btn("🔙 رجوع", callback_data="m")],
    ])
    return text, kb


async def _show(query, view):
    text, kb = view
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


CANCEL_KB = Markup([[Btn("❌ إلغاء", callback_data="adm:panel")]])


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(context, uid):
        await q.answer("للأدمن بس 🔒", show_alert=True)
        return

    if action == "panel":
        context.user_data.pop("bc_src", None)
        await _show(q, panel_view(context))

    elif action == "bc":
        context.user_data["awaiting"] = {"type": "bc"}
        await _show(q, ("📣 ابعت دلوقتي الرسالة اللي عايز توصلها لكل المستخدمين.\n"
                        "ممكن تكون نص أو صورة أو فيديو أو ملف (مع كابشن). هتشوف معاينة وتأكّد قبل ما تتبعت.", CANCEL_KB))

    elif action == "go":
        src = context.user_data.pop("bc_src", None)
        if not src:
            await _show(q, ("مفيش رسالة جاهزة للإرسال. ابدأ من جديد.", CANCEL_KB))
            return
        if context.application.bot_data.get("bc_running"):
            await q.message.reply_text("فيه برودكاست شغّال دلوقتي، استنى لما يخلّص ⏳")
            return
        status = await q.message.reply_text("⏳ جاري الإرسال...")
        task = asyncio.create_task(run_broadcast(context.application, src, status))
        context.application.bot_data["bc_running"] = task          # مرجع عشان التاسك ما يتمسحش
        task.add_done_callback(lambda _t: context.application.bot_data.pop("bc_running", None))
        await _show(q, ("تم بدء الإرسال ✅", Markup([[Btn("🔙 لوحة الأدمن", callback_data="adm:panel")]])))

    elif action == "csv":
        await send_users_csv(q.message, context)

    elif action == "imp":
        context.user_data["awaiting"] = {"type": "import"}
        await _show(q, (IMPORT_PROMPT, CANCEL_KB))


async def capture_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الأدمن بعت الرسالة بعد ما دوس «برودكاست»: نعرض معاينة ونطلب التأكيد."""
    msg = update.message
    if not is_admin(context, update.effective_user.id):
        context.user_data.pop("awaiting", None)
        return
    context.user_data.pop("awaiting", None)
    context.user_data["bc_src"] = (msg.chat_id, msg.message_id)
    n = len(_store(context).broadcast_targets())
    await context.bot.copy_message(chat_id=msg.chat_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
    await msg.reply_text(
        f"👆 دي معاينة الرسالة. هتتبعت لـ {n} مستخدم.\nتأكّد وابعت:",
        reply_markup=Markup([[Btn(f"✅ ابعت لـ {n} مستخدم", callback_data="adm:go")],
                             [Btn("❌ إلغاء", callback_data="adm:panel")]]))


async def run_broadcast(app, src, status_msg):
    store = app.bot_data["store"]
    src_chat, src_msg = src
    targets = store.broadcast_targets()
    sent = failed = blocked = 0
    for i, uid in enumerate(targets, 1):
        for attempt in range(2):
            try:
                await app.bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_msg)
                sent += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except Forbidden:
                blocked += 1
                if uid in store.users:
                    store.users[uid]["blocked"] = True
                    store.mark()
            except TelegramError as e:
                failed += 1
                log.warning("broadcast to %s failed: %s", uid, e)
            break
        await asyncio.sleep(SEND_DELAY)
        if i % 40 == 0:
            try:
                await status_msg.edit_text(f"⏳ جاري الإرسال... {i}/{len(targets)}")
            except TelegramError:
                pass
    report = (f"📣 خلص البرودكاست\n\n✅ وصل: {sent}\n🚫 عاملين بلوك: {blocked}\n❌ فشل: {failed}\n"
              f"👥 الإجمالي: {len(targets)}")
    try:
        await status_msg.edit_text(report)
    except TelegramError:
        await app.bot.send_message(src_chat, report)


IMPORT_PROMPT = ("📥 ابعت أرقام (IDs) المستخدمين القدام: كل رقم في سطر أو مفصولين بمسافة/فاصلة، "
                 "أو ابعت ملف txt/csv فيه الأرقام.")


def parse_ids(text: str) -> list:
    return list(dict.fromkeys(int(x) for x in re.findall(r"(?<!\d)\d{5,15}(?!\d)", text)))


async def _do_import(message, context, ids: list):
    from feed_core import default_quantities
    feeds = context.application.bot_data["feeds"]
    added, existing = _store(context).import_ids(ids, default_quantities(feeds))
    await message.reply_text(
        f"✅ اتضاف {added} مستخدم جديد" + (f" ({existing} كانوا موجودين)" if existing else "") + ".\n"
        "ملحوظة: تيليجرام بيسمح للبوت يبعت بس للي سبق وبدأوا المحادثة معاه. اللي مش هيوصلهم برودكاست "
        "بيتعلّموا تلقائياً وبيتشالوا من القائمة بعد أول برودكاست.")


async def capture_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_admin(context, update.effective_user.id):
        context.user_data.pop("awaiting", None)
        return
    text = msg.text or ""
    if msg.document:
        if (msg.document.file_size or 0) > 5_000_000:
            await msg.reply_text("الملف كبير أوي (الحد 5MB).")
            return
        f = await msg.document.get_file()
        text += " " + bytes(await f.download_as_bytearray()).decode("utf-8", errors="ignore")
    ids = parse_ids(text)
    if not ids:
        await msg.reply_text("مالقيتش أرقام صالحة. ابعت الأرقام (5 أرقام فأكتر) أو اضغط إلغاء.", reply_markup=CANCEL_KB)
        return
    context.user_data.pop("awaiting", None)
    await _do_import(msg, context, ids)


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(context, update.effective_user.id):
        return
    ids = parse_ids(" ".join(context.args or []))
    if ids:
        await _do_import(update.message, context, ids)
        return
    context.user_data["awaiting"] = {"type": "import"}
    await update.message.reply_text(IMPORT_PROMPT, reply_markup=CANCEL_KB)


async def send_users_csv(message, context):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "username", "joined_utc", "last_seen_utc", "calcs", "blocked", "banned", "imported"])
    for uid, u in _store(context).users.items():
        w.writerow([uid, u["name"], u["username"], _ts(u["joined"]), _ts(u["last_seen"]),
                    u.get("calcs", 0), int(bool(u.get("blocked"))), int(bool(u.get("banned"))), int(bool(u.get("imported")))])
    await message.reply_document(document=buf.getvalue().encode("utf-8-sig"), filename="users.csv",
                                 caption=f"👥 {len(_store(context).users)} مستخدم")


# ----------------------------- الأوامر -----------------------------
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(context, update.effective_user.id):
        return
    text, kb = panel_view(context)
    await update.message.reply_text(text, reply_markup=kb)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(context, update.effective_user.id):
        return
    context.user_data["awaiting"] = {"type": "bc"}
    await update.message.reply_text("📣 ابعت الرسالة اللي عايز توصلها لكل المستخدمين (نص/صورة/فيديو/ملف):",
                                    reply_markup=CANCEL_KB)


SOURCES = {"fb": "فيسبوك", "wa": "واتساب", "tg": "تيليجرام", "yt": "يوتيوب", "tt": "تيك توك",
           "ig": "إنستجرام", "x": "تويتر/X", "grp": "جروبات"}


async def cmd_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(context, update.effective_user.id):
        return
    uname = context.application.bot_data.get("username")
    if not uname:
        await update.message.reply_text("مش عارف يوزر البوت لسه، جرّب بعد شوية.")
        return
    lines = "\n".join(f"{name}: https://t.me/{uname}?start={key}" for key, name in SOURCES.items())
    await update.message.reply_text(
        "🔗 لينكات التتبع — كل لينك بيسجّل المستخدم الجديد على مصدره وبيظهر في لوحة الأدمن:\n\n" + lines +
        "\n\nتقدر تعمل أي كود تاني للحملة: ?start=اسم_حملة (حروف إنجليزي وأرقام و _ بس).",
        disable_web_page_preview=True)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(context, update.effective_user.id):
        await send_users_csv(update.message, context)


async def _set_ban(update, context, banned: bool):
    if not is_admin(context, update.effective_user.id):
        return
    try:
        target = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(f"الاستخدام: /{'ban' if banned else 'unban'} رقم_المستخدم")
        return
    u = _store(context).users.get(target)
    if not u:
        await update.message.reply_text("مفيش مستخدم بالرقم ده.")
        return
    if target in context.application.bot_data["admin_ids"]:
        await update.message.reply_text("مينفعش تحظر أدمن 😅")
        return
    u["banned"] = banned
    _store(context).mark()
    await update.message.reply_text(("⛔ تم حظر " if banned else "✅ تم فك الحظر عن ") + f"{u['name'] or target} ({target})")


async def cmd_ban(update, context):
    await _set_ban(update, context, True)


async def cmd_unban(update, context):
    await _set_ban(update, context, False)


def handlers():
    return [CommandHandler("admin", cmd_admin), CommandHandler("broadcast", cmd_broadcast),
            CommandHandler("users", cmd_users), CommandHandler("import", cmd_import), CommandHandler("links", cmd_links), CommandHandler("ban", cmd_ban), CommandHandler("unban", cmd_unban)]
