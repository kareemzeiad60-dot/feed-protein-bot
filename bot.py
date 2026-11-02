"""
بوت تيليجرام لحساب البروتين في العليقة - بالأزرار.
اختار الخامة -> اظبط الكمية بأزرار +/- -> احسب -> يوصلك ملف الإكسل متعدّل (بنسب المكونات والتكلفة).

مميزات: اشتراك إجباري في قناة • بروتين مستهدف مع نصيحة • تحجيم الخلطة • حفظ خلطاتك •
        أسعار وتكلفة الطن • لوحة أدمن وبرودكاست • حفظ البيانات بعد إعادة التشغيل.
"""
import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from urllib.parse import quote

from telegram import (BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault,
                      InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, MenuButtonCommands, MenuButtonDefault,
                      ReplyKeyboardRemove, Update)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

import admin
import breed
import feed_core as fc
from storage import MAX_RECIPES, Store

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("feedbot")

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.environ.get("TEMPLATE_PATH", os.path.join(BASE, "Feed_Calculator_Bilingual.xlsx"))
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(BASE, "bot_data.json"))
ADMIN_IDS = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_IDS", ""))]
CHANNEL = os.environ.get("CHANNEL_USERNAME", "@KIRA349A")
CHANNEL_URL = f"https://t.me/{CHANNEL.lstrip('@')}"
REQUIRE_SUB = os.environ.get("REQUIRE_SUBSCRIPTION", "1") != "0"
# قائمة الأوامر اللي جنب خانة الكتابة (زرار Menu). COMMAND_MENU=0 يشيلها خالص.
COMMAND_MENU = os.environ.get("COMMAND_MENU", "1") != "0"
WATERMARK = os.environ.get("WATERMARK", "1") != "0"        # رابط البوت تحت جدول الإكسل وفي الكابشن
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")          # بيتعرف تلقائياً من تيليجرام عند التشغيل
SHARE_TEXT = "🐄 بوت بيحسب البروتين ونسب المكونات وتكلفة العليقة ويبعتلك ملف إكسل جاهز — جرّبه!"
# إعادة الحساب بـ LibreOffice (لو متسطّب) عشان القيم تظهر في معاينة الموبايل. اقفلها بـ RECALC=0
RECALC = os.environ.get("RECALC", "1") != "0" and bool(shutil.which("soffice") or shutil.which("libreoffice"))
FEEDS = fc.load_feeds(TEMPLATE)
# موديل التعرّف على السلالة (اختياري: لو الملفات أو TensorFlow مش موجودين الميزة بتتقفل لوحدها)
CLASSIFIER = breed.BreedClassifier(os.environ.get("CATTLE_MODEL_DIR", os.path.join(BASE, "models")),
                                   os.environ.get("CATTLE_MODEL_FILE", "cattle_model.h5"))
BREED_COOLDOWN = 4                  # ثواني بين صورة وصورة لنفس المستخدم
MAX_IMAGE_BYTES = 15_000_000
_breed_sem = asyncio.Semaphore(1)   # تحليل صورة واحدة في المرة (الموديل بياخد CPU)
BY_ROW = {f.row: f for f in FEEDS}
PAGE = 8                                  # عدد الخامات في الصفحة الواحدة
MAX_QTY = 1_000_000
SUB_CACHE_SECONDS = 600

WELCOME = (
    "أهلاً بيك 👋\n"
    "أنا بحسب كمية ونسبة البروتين في العليقة وببعتلك ملف إكسل متعدّل (بنسب المكونات والتكلفة).\n\n"
    "دوس «➕ إضافة / تعديل خامة»، اختار الخامة وحدد الكمية بالأزرار، وفي الآخر دوس «✅ احسب».\n"
    "الإضافات (ملح، بريمكس، بيكربونات...) متحطّة بجرعتها القياسية وتقدر تعدّلها."
)
HELP = (
    WELCOME + "\n\n"
    "✍️ وتقدر تكتب بدل الأزرار، كل خامة في سطر:\n"
    "ذرة صفراء 500\nكسب فول صويا 250\nsoybean meal 250\n\n"
    "🎯 الهدف: حدد بروتين مستهدف وهقولك تضيف كام.\n"
    "⚖️ تحجيم: حوّل الخلطة لأي وزن (مثلاً طن).\n"
    "💾 حفظ / 📂 خلطاتي: احفظ خلطاتك وارجعلها.\n"
    "💵 الأسعار: ادخل سعر الطن لكل خامة وهحسبلك تكلفة الخلطة.\n\n"
    "/feeds — أسماء الخامات   |   /new — ابدأ من جديد   |   /calc — احسب   |   /id — رقمك"
)


# ----------------------------------------------------------------------------
# مساعدات
# ----------------------------------------------------------------------------
def S(context) -> Store:
    return context.application.bot_data["store"]


def me(update: Update, context):
    uid = update.effective_user.id
    return S(context).user(uid, fc.default_quantities(FEEDS)), uid


def is_admin(context, uid) -> bool:
    return uid in context.application.bot_data.get("admin_ids", [])


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def short(x: float) -> str:
    return f"{x:g}"


RLM = "\u200f"          # علامة اتجاه من اليمين: بتثبّت كل سطر RTL حتى لو بدأ برقم أو إيموجي


def rtl(text: str) -> str:
    """كل سطر بيتثبّت من اليمين عشان الأرقام والنسب ما تلخبطش السطر."""
    return "\n".join(RLM + line if line.strip() else line for line in text.split("\n"))


def summary_text(res: dict, title: str, target=None) -> str:
    items = "\n".join(
        f"• {f.name_ar}\n   {fmt(q)} كجم • {share * 100:.2f}% • بروتين {fmt(p)} كجم"
        for f, q, p, share in res["lines"])
    out = (f"{title}\n\n{items}\n\n"
           f"━━━━━━━━━━\n"
           f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم\n"
           f"🥩 كمية البروتين: {fmt(res['protein_kg'])} كجم\n"
           f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%")
    if target:
        _, adv = fc.advice(res, target, FEEDS)
        if adv:
            out += "\n\n" + adv
    if res["cost_per_ton"] is not None:
        out += f"\n\n💵 تكلفة طن الخلطة: {fmt(res['cost_per_ton'])} جنيه"
        if res["missing_price"]:
            out += "\n⚠️ ناقص سعر: " + "، ".join(f.name_ar for f in res["missing_price"])
    return rtl(out)


# ----------------------------------------------------------------------------
# الشاشات (نص + أزرار)
# ----------------------------------------------------------------------------
def menu_view(u, admin_flag=False, title="🧮 الخلطة الحالية:"):
    res = fc.compute(u["qty"], FEEDS, u["prices"])
    text = summary_text(res, title, u.get("target")) if res["lines"] else "لسه مفيش خامات في الخلطة. دوس «إضافة» وابدأ 👇"
    rows = [
        [Btn("➕ إضافة / تعديل خامة", callback_data="a:0")],
        [Btn("✅ احسب وابعتلي الملف", callback_data="calc")],
        [Btn("🐄 تعرّف على السلالة من صورة", callback_data="br")],
        [Btn("🎯 البروتين المستهدف", callback_data="tg"), Btn("⚖️ تحجيم الخلطة", callback_data="sc")],
        [Btn("💾 حفظ الخلطة", callback_data="sv"), Btn("📂 خلطاتي", callback_data="rc")],
        [Btn("🔄 ابدأ من جديد", callback_data="reset")],
    ]
    if BOT_USERNAME:
        link = quote(f"https://t.me/{BOT_USERNAME}?start=share", safe="")
        rows[-1].append(Btn("📣 شارك البوت", url=f"https://t.me/share/url?url={link}&text={quote(SHARE_TEXT)}"))
    if admin_flag:
        rows.append([Btn("🛠 لوحة الأدمن", callback_data="adm:panel")])
    return text, Markup(rows)


def list_view(u, page: int):
    pages = (len(FEEDS) + PAGE - 1) // PAGE
    page = max(0, min(page, pages - 1))
    rows = []
    for f in FEEDS[page * PAGE:(page + 1) * PAGE]:
        q = u["qty"].get(f.row, 0)
        label = f"{f.name_ar}  ✓ {short(q)} كجم" if q > 0 else f.name_ar
        rows.append([Btn(label, callback_data=f"s:{f.row}")])
    nav = []
    if page > 0:
        nav.append(Btn("◀️ السابق", callback_data=f"a:{page - 1}"))
    if page < pages - 1:
        nav.append(Btn("التالي ▶️", callback_data=f"a:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([Btn("🔙 رجوع للخلطة", callback_data="m")])
    return f"اختار الخامة (صفحة {page + 1} من {pages}):", Markup(rows)


def qty_view(u, row: int):
    f = BY_ROW[row]
    q = u["qty"].get(row, 0)
    price = u["prices"].get(row, 0)
    text = rtl(
        f"🔹 {f.name_ar}\n\n"
        f"⚖️ الكمية: {fmt(q)} كجم\n"
        f"🥩 البروتين في الخامة: {f.cp_as_fed * 100:.2f}%\n"
        f"🥩 بروتين الكمية دي: {fmt(q * f.cp_as_fed)} كجم\n"
        f"💵 سعر الطن: {fmt(price) + ' جنيه' if price > 0 else 'مش متحدد'}\n\n"
        "اظبط الكمية بالأزرار أو دوس «✍️ اكتب رقم»:"
    )
    d = lambda n: Btn(f"{'➕' if n > 0 else '➖'}{abs(n)}", callback_data=f"d:{row}:{n}")
    kb = Markup([
        [d(1), d(10), d(100), d(500)],
        [d(-1), d(-10), d(-100), d(-500)],
        [Btn("✍️ اكتب رقم", callback_data=f"t:{row}"), Btn("0️⃣ صفّر / احذف", callback_data=f"d:{row}:0")],
        [Btn("💵 سعر الطن", callback_data=f"pr:{row}")],
        [Btn("📋 خامة تانية", callback_data=f"a:{(row - fc.FIRST_ROW) // PAGE}"), Btn("✅ رجوع للخلطة", callback_data="m")],
    ])
    return text, kb


BREED_KB = Markup([[Btn("📸 صورة تانية", callback_data="br"), Btn("🔙 القائمة", callback_data="m")]])


def breed_view():
    if CLASSIFIER.available:
        text = ("📸 ابعتلي صورة واضحة للحيوان (جنب كامل ولو ينفع في إضاءة كويسة) وهقولك السلالة المتوقعة.\n"
                "تقدر تبعتها كصورة عادية أو كملف.")
    else:
        text = "🐄 ميزة التعرّف على السلالة مش متاحة دلوقتي. جرّب بعد شوية."
    return rtl(text), Markup([[Btn("🔙 رجوع", callback_data="m")]])


def target_view(u):
    cur = u.get("target")
    text = "🎯 اختار نسبة البروتين المستهدفة للخلطة:" + (f"\nالهدف الحالي: {cur:g}%" if cur else "")
    b = lambda n: Btn(f"{n}%", callback_data=f"tg:{n}")
    kb = Markup([
        [b(14), b(16), b(18)], [b(20), b(22), b(24)],
        [Btn("✍️ اكتب رقم", callback_data="tg:x"), Btn("🚫 إلغاء الهدف", callback_data="tg:0")],
        [Btn("🔙 رجوع", callback_data="m")],
    ])
    return text, kb


def scale_view(u):
    total = sum(u["qty"].values())
    text = f"⚖️ إجمالي الخلطة دلوقتي: {fmt(total)} كجم\nاختار الإجمالي الجديد وهكبّر/أصغّر كل الكميات بنفس النسبة:"
    b = lambda n: Btn(f"{n} كجم" if n < 1000 else f"{n // 1000} طن", callback_data=f"sc:{n}")
    kb = Markup([[b(500), b(1000)], [b(2000), b(5000)],
                 [Btn("✍️ اكتب رقم", callback_data="sc:x")], [Btn("🔙 رجوع", callback_data="m")]])
    return text, kb


def recipes_view(u):
    names = list(u["recipes"])
    if not names:
        return "📂 لسه مفيش خلطات محفوظة. دوس «💾 حفظ الخلطة» من القائمة الرئيسية.", Markup([[Btn("🔙 رجوع", callback_data="m")]])
    rows = [[Btn(f"📋 {n}", callback_data=f"rl:{n}")] for n in names]
    rows.append([Btn("🔙 رجوع", callback_data="m")])
    return f"📂 خلطاتك المحفوظة ({len(names)}/{MAX_RECIPES}):", Markup(rows)


def recipe_view(u, name: str):
    saved = u["recipes"][name]                                  # KeyError لو اتمسحت = زرار قديم
    qty = {f.row: saved.get(f.row, 0.0) for f in FEEDS}
    res = fc.compute(qty, FEEDS, u["prices"])
    kb = Markup([[Btn("📥 استخدم الخلطة دي", callback_data=f"rg:{name}")],
                 [Btn("🗑 احذفها", callback_data=f"rd:{name}"), Btn("🔙 رجوع", callback_data="rc")]])
    return summary_text(res, f"📋 {name}"), kb


async def _show(query, view):
    text, kb = view
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ----------------------------------------------------------------------------
# بوابة الاشتراك الإجباري + الحظر + تسجيل المستخدمين (بتشتغل قبل أي هاندلر)
# ----------------------------------------------------------------------------
async def is_subscribed(context, uid: int, fresh: bool = False) -> bool:
    cache = context.application.bot_data.setdefault("sub_cache", {})
    if not fresh and time.time() - cache.get(uid, 0) < SUB_CACHE_SECONDS:
        return True
    try:
        m = await context.bot.get_chat_member(CHANNEL, uid)
    except TelegramError as e:
        # غالباً البوت مش أدمن في القناة. بنسيب المستخدمين يشتغلوا وبنبلّغ الأدمن مرة واحدة.
        log.error("subscription check failed: %s", e)
        bd = context.application.bot_data
        if not bd.get("sub_warned") and bd.get("admin_ids"):
            bd["sub_warned"] = True
            try:
                await context.bot.send_message(
                    bd["admin_ids"][0],
                    f"⚠️ مش قادر أتحقق من الاشتراك في {CHANNEL}.\nالسبب: {e}\n"
                    "ضيف البوت كأدمن في القناة. لحد ما تصلّحها الاشتراك الإجباري متوقّف مؤقتاً.")
            except TelegramError:
                pass
        return True
    ok = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER) \
        or (m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False))
    if ok:
        cache[uid] = time.time()
    return ok


SUB_KB = Markup([[Btn("📢 اشترك في القناة", url=CHANNEL_URL)],
                 [Btn("✅ اشتركت — تحقق", callback_data="chk")]])
SUB_TEXT = f"📢 لازم تشترك في القناة الأول عشان تستخدم البوت:\n{CHANNEL_URL}\n\nاشترك وبعدين دوس «✅ اشتركت — تحقق»."


def _start_payload(update: Update):
    """كود المصدر من لينك زي t.me/bot?start=fb (بيتسجّل مرة واحدة عند أول دخول)."""
    m = update.message
    if m and m.text and m.text.startswith("/start"):
        parts = m.text.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9_-]{1,24}", parts[1].strip()):
            return parts[1].strip().lower()
    return None


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None or user.is_bot:
        return
    u = S(context).touch(user.id, user.full_name, user.username, fc.default_quantities(FEEDS), _start_payload(update))
    if is_admin(context, user.id):
        return
    msg, q = update.message, update.callback_query
    if msg and msg.text and msg.text.split()[0].split("@")[0] == "/id":
        return
    if u.get("banned"):
        if q:
            await q.answer("🚫 تم حظرك من استخدام البوت", show_alert=True)
        elif msg:
            await msg.reply_text("🚫 تم حظرك من استخدام البوت.")
        raise ApplicationHandlerStop
    if not REQUIRE_SUB:
        return
    is_check = bool(q and q.data == "chk")
    if await is_subscribed(context, user.id, fresh=is_check):
        return
    if is_check:
        await q.answer("لسه مشتركتش في القناة ❗ اشترك الأول وبعدين دوس تحقق", show_alert=True)
    elif q:
        await q.answer()
        await q.message.reply_text(SUB_TEXT, reply_markup=SUB_KB)
    elif msg:
        await msg.reply_text(SUB_TEXT, reply_markup=SUB_KB)
    raise ApplicationHandlerStop


# ----------------------------------------------------------------------------
# الأوامر
# ----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, uid = me(update, context)
    context.user_data.pop("awaiting", None)
    text, kb = menu_view(u, is_admin(context, uid))
    # ReplyKeyboardRemove بيشيل أي كيبورد قديم لازق تحت خانة الكتابة (من نسخ قديمة)
    await update.message.reply_text(WELCOME, reply_markup=ReplyKeyboardRemove())
    try:                                                   # وبيصفّي أي زرار قائمة مخصص قديم في الشات ده
        await context.bot.set_chat_menu_button(chat_id=update.effective_chat.id,
                                               menu_button=MenuButtonCommands() if COMMAND_MENU else MenuButtonDefault())
    except TelegramError:
        pass
    await update.message.reply_text(text, reply_markup=kb)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    extra = "\n(أنت أدمن ✅)" if is_admin(context, uid) else ""
    await update.message.reply_text(f"رقمك (ID): {uid}{extra}")


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, uid = me(update, context)
    u["qty"] = fc.default_quantities(FEEDS)
    S(context).mark()
    context.user_data.pop("awaiting", None)
    text, kb = menu_view(u, is_admin(context, uid), "تمام، بدأنا من جديد ✨")
    await update.message.reply_text(text, reply_markup=kb)


async def feeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الخامات المتاحة:\n" + "\n".join(f"• {f.name_ar}  |  {f.name_en}" for f in FEEDS))


async def calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_file(update.message, update, context)


async def _send_file(chat_message, update, context: ContextTypes.DEFAULT_TYPE):
    u, uid = me(update, context)
    res = fc.compute(u["qty"], FEEDS, u["prices"])
    if res["total_weight"] <= 0:
        text, kb = menu_view(u, is_admin(context, uid))
        await chat_message.reply_text(text, reply_markup=kb)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Feed_Calculation.xlsx")
        footer = ((f"🤖 اتحسبت بواسطة بوت تيليجرام @{BOT_USERNAME} — t.me/{BOT_USERNAME}", f"https://t.me/{BOT_USERNAME}?start=xlsx")
                  if BOT_USERNAME and WATERMARK else None)
        await asyncio.to_thread(fc.build_workbook, TEMPLATE, u["qty"], path, RECALC, u["prices"], footer)
        caption = (f"🥩 البروتين: {fmt(res['protein_kg'])} كجم\n"
                   f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%\n"
                   f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم")
        if res["cost_per_ton"] is not None:
            caption += f"\n💵 تكلفة طن الخلطة: {fmt(res['cost_per_ton'])} جنيه"
        if BOT_USERNAME and WATERMARK:
            caption += f"\n\n🤖 @{BOT_USERNAME}"
        caption = rtl(caption)
        with open(path, "rb") as fh:
            await chat_message.reply_document(fh, filename="Feed_Calculation.xlsx", caption=caption)
    S(context).count_calc(uid)


# ----------------------------------------------------------------------------
# الأزرار
# ----------------------------------------------------------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u, uid = me(update, context)
    adm = is_admin(context, uid)
    kind, _, rest = q.data.partition(":")
    context.user_data.pop("awaiting", None)
    store = S(context)
    try:
        if kind in ("m", "chk"):
            await _show(q, menu_view(u, adm))
        elif kind == "a":
            await _show(q, list_view(u, int(rest)))
        elif kind == "s":
            await _show(q, qty_view(u, int(rest)))
        elif kind == "d":
            row, delta = (int(x) for x in rest.split(":"))
            BY_ROW[row]
            u["qty"][row] = 0.0 if delta == 0 else min(MAX_QTY, max(0.0, round(u["qty"].get(row, 0) + delta, 2)))
            store.mark()
            await _show(q, qty_view(u, row))
        elif kind == "t":
            row = int(rest)
            context.user_data["awaiting"] = {"type": "qty", "row": row}
            await _show(q, (f"اكتب الكمية بالكيلو لـ «{BY_ROW[row].name_ar}» (رقم بس، مثلاً 250 أو 7.5):",
                            Markup([[Btn("❌ إلغاء", callback_data=f"s:{row}")]])))
        elif kind == "pr":
            row = int(rest)
            context.user_data["awaiting"] = {"type": "price", "row": row}
            await _show(q, (f"اكتب سعر الطن (بالجنيه) لـ «{BY_ROW[row].name_ar}» — رقم بس، مثلاً 14500.\nاكتب 0 لمسح السعر.",
                            Markup([[Btn("❌ إلغاء", callback_data=f"s:{row}")]])))
        elif kind == "calc":
            await _send_file(q.message, update, context)
        elif kind == "reset":
            u["qty"] = fc.default_quantities(FEEDS)
            store.mark()
            await _show(q, menu_view(u, adm, "تمام، بدأنا من جديد ✨"))
        elif kind == "br":
            await _show(q, breed_view())
        elif kind == "tg":
            if rest == "":
                await _show(q, target_view(u))
            elif rest == "x":
                context.user_data["awaiting"] = {"type": "target"}
                await _show(q, ("اكتب نسبة البروتين المستهدفة (رقم بس، مثلاً 17.5):",
                                Markup([[Btn("❌ إلغاء", callback_data="tg")]])))
            else:
                u["target"] = float(rest) or None
                store.mark()
                await _show(q, menu_view(u, adm, "🎯 تم تحديد الهدف" if u["target"] else "تم إلغاء الهدف"))
        elif kind == "sc":
            if rest == "":
                await _show(q, scale_view(u))
            elif rest == "x":
                context.user_data["awaiting"] = {"type": "scale"}
                await _show(q, ("اكتب الإجمالي الجديد بالكيلو (رقم بس، مثلاً 3000):",
                                Markup([[Btn("❌ إلغاء", callback_data="sc")]])))
            else:
                await _show(q, _apply_scale(u, adm, float(rest)))
                store.mark()
        elif kind == "sv":
            context.user_data["awaiting"] = {"type": "recipe"}
            await _show(q, (f"💾 اكتب اسم للخلطة دي (حد أقصى 20 حرف):\nمحفوظ عندك {len(u['recipes'])}/{MAX_RECIPES}",
                            Markup([[Btn("❌ إلغاء", callback_data="m")]])))
        elif kind == "rc":
            await _show(q, recipes_view(u))
        elif kind == "rl":
            await _show(q, recipe_view(u, rest))
        elif kind == "rg":
            saved = u["recipes"][rest]
            u["qty"] = {f.row: saved.get(f.row, 0.0) for f in FEEDS}
            store.mark()
            await _show(q, menu_view(u, adm, f"📥 تم تحميل «{rest}»"))
        elif kind == "rd":
            u["recipes"].pop(rest, None)
            store.mark()
            await _show(q, recipes_view(u))
        elif kind == "adm":
            await admin.on_callback(update, context, rest)
        else:
            raise KeyError(kind)
    except (KeyError, ValueError, IndexError):
        log.info("stale/invalid button: %r", q.data)
        await q.message.reply_text("الزرار ده قديم 🙈 دوس /start عشان تفتح القائمة من جديد.")


def _apply_scale(u, adm, new_total: float):
    if sum(u["qty"].values()) <= 0:
        return menu_view(u, adm, "الخلطة فاضية، أضف خامات الأول")
    u["qty"] = fc.scale_quantities(u["qty"], new_total)
    return menu_view(u, adm, f"⚖️ تم تحجيم الخلطة لـ {fmt(new_total)} كجم")


# ----------------------------------------------------------------------------
# الكتابة (بديل للأزرار + إدخال الأرقام والأسماء)
# ----------------------------------------------------------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    u, uid = me(update, context)
    adm = is_admin(context, uid)
    store = S(context)
    aw = context.user_data.get("awaiting")

    if aw and aw["type"] == "bc":
        await admin.capture_broadcast(update, context)
        return
    if aw and aw["type"] == "import":
        await admin.capture_import(update, context)
        return
    text = msg.text
    if not text:
        t, kb = menu_view(u, adm)
        await msg.reply_text("ابعتلي نص بس، أو استخدم الأزرار 👇\n\n" + t, reply_markup=kb)
        return

    if aw:
        kind = aw["type"]
        if kind == "recipe":
            name = " ".join(text.split())[:20]
            if not name or name.startswith("/"):
                await msg.reply_text("اكتب اسم عادي للخلطة 🙏")
                return
            if name not in u["recipes"] and len(u["recipes"]) >= MAX_RECIPES:
                await msg.reply_text(f"وصلت للحد الأقصى ({MAX_RECIPES} خلطة). امسح واحدة من «📂 خلطاتي» الأول.")
                context.user_data.pop("awaiting", None)
                return
            u["recipes"][name] = {r: q for r, q in u["qty"].items() if q > 0}
            store.mark()
            context.user_data.pop("awaiting", None)
            t, kb = menu_view(u, adm, f"💾 اتحفظت باسم «{name}»")
            await msg.reply_text(t, reply_markup=kb)
            return
        value = fc.parse_number(text)
        limits = {"qty": (0, MAX_QTY), "price": (0, MAX_QTY), "target": (0.01, 60), "scale": (0.01, MAX_QTY)}
        lo, hi = limits[kind]
        if value is None or not (lo <= value <= hi):
            await msg.reply_text("ابعت رقم صحيح بس 🙏" + (f" (بين {lo:g} و{hi:g})" if kind == "target" else ""))
            return
        context.user_data.pop("awaiting", None)
        if kind == "qty":
            u["qty"][aw["row"]] = value
            store.mark()
            t, kb = qty_view(u, aw["row"])
        elif kind == "price":
            u["prices"][aw["row"]] = value
            store.mark()
            t, kb = qty_view(u, aw["row"])
        elif kind == "target":
            u["target"] = value
            store.mark()
            t, kb = menu_view(u, adm, "🎯 تم تحديد الهدف")
        else:
            t, kb = _apply_scale(u, adm, value)
            store.mark()
        await msg.reply_text(t, reply_markup=kb)
        return

    updates, errors = fc.parse_message(text, FEEDS)   # كتابة حرة: "ذرة صفراء 500"
    u["qty"].update(updates)
    if updates:
        store.mark()
    t, kb = menu_view(u, adm, "تمام سجّلت 👍 الخلطة الحالية:" if updates else "🧮 الخلطة الحالية:")
    if errors:
        t += "\n\n⚠️ الأسطر دي مافهمتهاش:\n" + "\n".join(errors)
    if not updates and not errors:
        t = "استخدم الأزرار 👇\n\n" + t
    await msg.reply_text(t, reply_markup=kb)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صورة (أو ملف صورة) ← تعرّف على السلالة."""
    aw = context.user_data.get("awaiting")
    if aw and aw["type"] == "bc":                       # الأدمن بيبعت صورة للبرودكاست
        await on_message(update, context)
        return
    msg = update.message
    u, uid = me(update, context)
    if not CLASSIFIER.available:
        await msg.reply_text(breed_view()[0])
        return
    now = time.time()
    if now - context.user_data.get("last_breed", 0) < BREED_COOLDOWN:
        await msg.reply_text("استنى كام ثانية وابعت الصورة اللي بعدها ⏳")
        return
    context.user_data["last_breed"] = now
    file_obj = msg.photo[-1] if msg.photo else msg.document
    if (file_obj.file_size or 0) > MAX_IMAGE_BYTES:
        await msg.reply_text("الصورة كبيرة أوي 😅 ابعتها كصورة عادية أو صغّر حجمها.")
        return
    wait = await msg.reply_text("⏳ جاري تحليل الصورة...")
    try:
        f = await file_obj.get_file()
        data = bytes(await f.download_as_bytearray())
        async with _breed_sem:
            preds = await asyncio.to_thread(CLASSIFIER.predict, data)
    except Exception:
        log.exception("breed prediction failed")
        await wait.edit_text("مقدرتش أقرا الصورة دي 😕 ابعت صورة JPG أو PNG واضحة.")
        return
    u["breed_scans"] = u.get("breed_scans", 0) + 1
    S(context).mark()
    await wait.edit_text(rtl(breed.format_result(preds, len(CLASSIFIER.labels))), reply_markup=BREED_KB)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("حصل خطأ غير متوقع 😕 جرّب تاني بعد شوية.")
        except TelegramError:
            pass


# ----------------------------------------------------------------------------
# التشغيل
# ----------------------------------------------------------------------------
async def flush_loop(app: Application):
    while True:
        await asyncio.sleep(120)
        try:
            await app.bot_data["store"].flush(app.bot)
        except Exception:
            log.exception("flush failed")


async def post_init(app: Application):
    store: Store = app.bot_data["store"]
    store.load_local()
    for attempt in range(3):                        # استعادة النسخة الاحتياطية من تيليجرام (لو موجودة)
        if await store.restore_from_telegram(app.bot):
            break
        await asyncio.sleep(2)
    app.bot_data["flush_task"] = asyncio.create_task(flush_loop(app))
    global BOT_USERNAME
    try:
        BOT_USERNAME = BOT_USERNAME or (await app.bot.get_me()).username or ""
    except TelegramError as e:
        log.warning("get_me failed: %s", e)
    app.bot_data["username"] = BOT_USERNAME
    app.bot_data["breed_task"] = asyncio.create_task(_load_breed())      # في الخلفية عشان البوت يرد فوراً
    await reset_menus(app)


async def _load_breed():
    ok = await asyncio.to_thread(CLASSIFIER.load)
    log.info("breed recognition: %s", "ON" if ok else f"OFF ({CLASSIFIER.error})")


async def reset_menus(app: Application):
    """بيمسح أي قوايم أوامر/أزرار قديمة (كل النطاقات واللغات) وبيحط القائمة المحدّثة (أو مفيش لو COMMAND_MENU=0)."""
    bot, admins = app.bot, app.bot_data["admin_ids"]
    base = [BotCommand("start", "القائمة الرئيسية"), BotCommand("calc", "احسب وابعت الملف"),
            BotCommand("new", "خلطة جديدة"), BotCommand("help", "مساعدة")]
    extra = [BotCommand("admin", "لوحة الأدمن"), BotCommand("broadcast", "رسالة جماعية"), BotCommand("import", "استيراد مستخدمين"), BotCommand("links", "لينكات التتبع")]
    try:
        for scope in [BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()] + [BotCommandScopeChat(a) for a in admins]:
            for lang in (None, "ar", "en"):
                await bot.delete_my_commands(scope=scope, language_code=lang)
        if COMMAND_MENU:
            await bot.set_my_commands(base)
            for a in admins:
                await bot.set_my_commands(base + extra, scope=BotCommandScopeChat(a))
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands() if COMMAND_MENU else MenuButtonDefault())
    except TelegramError as e:
        log.warning("reset_menus failed: %s", e)


async def post_stop(app: Application):
    task = app.bot_data.get("flush_task")
    if task:
        task.cancel()
    try:
        await app.bot_data["store"].flush(app.bot, force=True)      # حفظ أخير قبل الإيقاف
    except Exception:
        log.exception("final flush failed")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).post_init(post_init).post_stop(post_stop).build()
    app.bot_data["store"] = Store(DATA_FILE, ADMIN_IDS)
    app.bot_data["admin_ids"] = ADMIN_IDS
    app.bot_data["feeds"] = FEEDS
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(CommandHandler("new", new))
    app.add_handler(CommandHandler("feeds", feeds_cmd))
    app.add_handler(CommandHandler("calc", calc))
    for h in admin.handlers():
        app.add_handler(h)
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط توكن البوت في متغير البيئة BOT_TOKEN (من @BotFather)")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS فاضي: ميزات الأدمن والنسخة الاحتياطية مقفولة. ابعت /id للبوت وحط رقمك في ADMIN_IDS")
    app = build_app(token)
    log.info("Bot started. admins=%s channel=%s require_sub=%s recalc=%s", ADMIN_IDS, CHANNEL, REQUIRE_SUB, RECALC)
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])


if __name__ == "__main__":
    main()
