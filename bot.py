"""
بوت تيليجرام لحساب البروتين في العليقة - بالأزرار.
اختار الخامة من الأزرار -> اظبط الكمية بأزرار +/- -> احسب -> البوت يبعتلك ملف الإكسل متعدّل.
(الكتابة لسه شغّالة كبديل: "ذرة صفراء 500")
"""
import asyncio
import logging
import os
import shutil
import tempfile

from telegram import BotCommand, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Update
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import feed_core as fc

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("feedbot")

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.environ.get("TEMPLATE_PATH", os.path.join(BASE, "Feed_Calculator_Bilingual.xlsx"))
# إعادة الحساب بـ LibreOffice (لو متسطّب) عشان القيم تظهر في معاينة الموبايل. اقفلها بـ RECALC=0
RECALC = os.environ.get("RECALC", "1") != "0" and bool(shutil.which("soffice") or shutil.which("libreoffice"))
FEEDS = fc.load_feeds(TEMPLATE)
BY_ROW = {f.row: f for f in FEEDS}
PAGE = 8                                  # عدد الخامات في الصفحة الواحدة
MAX_QTY = 1_000_000

WELCOME = (
    "أهلاً بيك 👋\n"
    "أنا بحسب كمية البروتين في العليقة وببعتلك ملف الإكسل متعدّل.\n\n"
    "دوس «➕ إضافة / تعديل خامة»، اختار الخامة وحدد الكمية بالأزرار، وفي الآخر دوس «✅ احسب».\n"
    "الإضافات (ملح، بريمكس، بيكربونات...) متحطّة بجرعتها القياسية وتقدر تعدّلها."
)
HELP = (
    WELCOME + "\n\n"
    "✍️ وتقدر تكتب بدل الأزرار، كل خامة في سطر:\n"
    "ذرة صفراء 500\nكسب فول صويا 250\nsoybean meal 250\n\n"
    "/feeds — أسماء الخامات   |   /new — ابدأ من جديد   |   /calc — احسب"
)


# ----------------------------------------------------------------------------
# الحالة والتنسيق
# ----------------------------------------------------------------------------
def _qty(context) -> dict:
    if "qty" not in context.user_data:
        context.user_data["qty"] = fc.default_quantities(FEEDS)
    return context.user_data["qty"]


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def short(x: float) -> str:
    return f"{x:g}"


def summary_text(res: dict, title: str) -> str:
    rows = "\n".join(f"• {f.name_ar}: {fmt(q)} كجم ← بروتين {fmt(p)} كجم" for f, q, p in res["lines"])
    return (
        f"{title}\n\n{rows}\n\n"
        f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم\n"
        f"🥩 كمية البروتين: {fmt(res['protein_kg'])} كجم\n"
        f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%"
    )


# ----------------------------------------------------------------------------
# الشاشات (نص + أزرار)
# ----------------------------------------------------------------------------
def menu_view(context, title="🧮 الخلطة الحالية:"):
    res = fc.compute(_qty(context), FEEDS)
    text = summary_text(res, title) if res["lines"] else "لسه مفيش خامات في الخلطة. دوس «إضافة» وابدأ 👇"
    kb = Markup([
        [Btn("➕ إضافة / تعديل خامة", callback_data="a:0")],
        [Btn("✅ احسب وابعتلي الملف", callback_data="calc")],
        [Btn("🔄 ابدأ من جديد", callback_data="reset")],
    ])
    return text, kb


def list_view(context, page: int):
    qty = _qty(context)
    pages = (len(FEEDS) + PAGE - 1) // PAGE
    page = max(0, min(page, pages - 1))
    rows = []
    for f in FEEDS[page * PAGE:(page + 1) * PAGE]:
        q = qty.get(f.row, 0)
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


def qty_view(context, row: int):
    f = BY_ROW[row]
    q = _qty(context).get(row, 0)
    text = (
        f"{f.name_ar}\n"
        f"الكمية الحالية: {fmt(q)} كجم\n"
        f"البروتين في الخامة: {f.cp_as_fed * 100:.2f}% ← بروتين الكمية دي: {fmt(q * f.cp_as_fed)} كجم\n\n"
        "اظبط الكمية بالأزرار أو دوس «اكتب رقم»:"
    )
    d = lambda n: Btn(f"{'➕' if n > 0 else '➖'}{abs(n)}", callback_data=f"d:{row}:{n}")
    kb = Markup([
        [d(1), d(10), d(100), d(500)],
        [d(-1), d(-10), d(-100), d(-500)],
        [Btn("✍️ اكتب رقم", callback_data=f"t:{row}"), Btn("0️⃣ صفّر / احذف", callback_data=f"d:{row}:0")],
        [Btn("📋 خامة تانية", callback_data=f"a:{(row - fc.FIRST_ROW) // PAGE}"), Btn("✅ رجوع للخلطة", callback_data="m")],
    ])
    return text, kb


async def _show(query, view):
    text, kb = view
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ----------------------------------------------------------------------------
# الأوامر
# ----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qty"] = fc.default_quantities(FEEDS)
    context.user_data.pop("awaiting", None)
    text, kb = menu_view(context)
    await update.message.reply_text(WELCOME)
    await update.message.reply_text(text, reply_markup=kb)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qty"] = fc.default_quantities(FEEDS)
    context.user_data.pop("awaiting", None)
    text, kb = menu_view(context, "تمام، بدأنا من جديد ✨")
    await update.message.reply_text(text, reply_markup=kb)


async def feeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الخامات المتاحة:\n" + "\n".join(f"• {f.name_ar}  |  {f.name_en}" for f in FEEDS))


async def calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_file(update.message, context)


async def _send_file(chat_message, context: ContextTypes.DEFAULT_TYPE):
    qty = _qty(context)
    res = fc.compute(qty, FEEDS)
    if res["total_weight"] <= 0:
        text, kb = menu_view(context)
        await chat_message.reply_text(text, reply_markup=kb)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Feed_Calculation.xlsx")
        await asyncio.to_thread(fc.build_workbook, TEMPLATE, qty, path, RECALC)
        caption = (f"🥩 البروتين: {fmt(res['protein_kg'])} كجم\n"
                   f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%\n"
                   f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم")
        with open(path, "rb") as fh:
            await chat_message.reply_document(fh, filename="Feed_Calculation.xlsx", caption=caption)


# ----------------------------------------------------------------------------
# الأزرار
# ----------------------------------------------------------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kind, *args = q.data.split(":")
    qty = _qty(context)
    context.user_data.pop("awaiting", None)

    if kind == "m":                                   # القائمة الرئيسية
        await _show(q, menu_view(context))
    elif kind == "a":                                 # قائمة الخامات (صفحة)
        await _show(q, list_view(context, int(args[0])))
    elif kind == "s":                                 # اختيار خامة
        await _show(q, qty_view(context, int(args[0])))
    elif kind == "d":                                 # تغيير الكمية (+/-/صفّر)
        row, delta = int(args[0]), int(args[1])
        new_q = 0.0 if delta == 0 else min(MAX_QTY, max(0.0, round(qty.get(row, 0) + delta, 2)))
        qty[row] = new_q
        await _show(q, qty_view(context, row))
    elif kind == "t":                                 # كتابة رقم
        row = int(args[0])
        context.user_data["awaiting"] = row
        await _show(q, (f"اكتب الكمية بالكيلو لـ «{BY_ROW[row].name_ar}» (رقم بس، مثلاً 250 أو 7.5):",
                        Markup([[Btn("❌ إلغاء", callback_data=f"s:{row}")]])))
    elif kind == "calc":
        await _send_file(q.message, context)
    elif kind == "reset":
        context.user_data["qty"] = fc.default_quantities(FEEDS)
        await _show(q, menu_view(context, "تمام، بدأنا من جديد ✨"))


# ----------------------------------------------------------------------------
# الكتابة (بديل للأزرار)
# ----------------------------------------------------------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = _qty(context)
    text = update.message.text

    row = context.user_data.get("awaiting")           # المستخدم دوس «اكتب رقم»
    if row is not None:
        value = fc.parse_number(text)
        if value is None or value > MAX_QTY:
            await update.message.reply_text("ابعت رقم بس بالكيلو، مثلاً 250 أو 7.5 🙏")
            return
        qty[row] = value
        context.user_data.pop("awaiting", None)
        t, kb = qty_view(context, row)
        await update.message.reply_text(t, reply_markup=kb)
        return

    updates, errors = fc.parse_message(text, FEEDS)   # كتابة حرة: "ذرة صفراء 500"
    qty.update(updates)
    t, kb = menu_view(context, "تمام سجّلت 👍 الخلطة الحالية:" if updates else "🧮 الخلطة الحالية:")
    if errors:
        t += "\n\n⚠️ الأسطر دي مافهمتهاش:\n" + "\n".join(errors)
    if not updates and not errors:
        t = "استخدم الأزرار 👇\n\n" + t
    await update.message.reply_text(t, reply_markup=kb)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("حصل خطأ غير متوقع 😕 جرّب تاني بعد شوية.")


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "ابدأ"), BotCommand("calc", "احسب وابعت الملف"),
        BotCommand("new", "ابدأ خلطة جديدة"), BotCommand("feeds", "أسماء الخامات"),
        BotCommand("help", "مساعدة"),
    ])


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط توكن البوت في متغير البيئة BOT_TOKEN (من @BotFather)")
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", new))
    app.add_handler(CommandHandler("feeds", feeds_cmd))
    app.add_handler(CommandHandler("calc", calc))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("Bot started. LibreOffice recalc: %s", RECALC)
    app.run_polling()


if __name__ == "__main__":
    main()
