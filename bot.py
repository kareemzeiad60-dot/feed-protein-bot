"""
بوت تيليجرام لحساب البروتين في العليقة.
المستخدم يبعت الخامات والكميات -> البوت يعدّل ملف الإكسل -> يبعتهوله ومعاه ملخص البروتين.
"""
import asyncio
import logging
import os
import shutil
import tempfile

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ احسب وابعتلي الملف", callback_data="calc"),
    InlineKeyboardButton("🔄 ابدأ من جديد", callback_data="reset"),
]])

HELP = (
    "أهلاً بيك 👋\n"
    "أنا بحسب كمية البروتين في العليقة وبعدّلك ملف الإكسل وأبعتهولك.\n\n"
    "ابعتلي الخامات والكميات (بالكيلو)، كل خامة في سطر، مثلاً:\n"
    "ذرة صفراء 500\n"
    "كسب فول صويا 250\n"
    "ردة 100\n"
    "soybean meal 250   ← بالإنجليزي عادي\n\n"
    "• أي خامة مبعتهاش بتتحسب صفر.\n"
    "• الإضافات (بيكربونات، بريمكس، ملح، حجر جيري، مضاد سموم، خمائر) بتتحط بالجرعة القياسية "
    "الموجودة في الملف، وتقدر تغيّرها أو تخليها 0 (مثلاً: ملح 0).\n"
    "• لو بعت نفس الخامة تاني الكمية الجديدة بتحل محل القديمة.\n\n"
    "لما تخلّص دوس «احسب» أو ابعت /calc\n"
    "/feeds — أسماء الخامات   |   /new — ابدأ من جديد"
)


def _qty(context) -> dict:
    if "qty" not in context.user_data:
        context.user_data["qty"] = fc.default_quantities(FEEDS)
    return context.user_data["qty"]


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def summary_text(res: dict, title: str) -> str:
    rows = "\n".join(f"• {f.name_ar}: {fmt(q)} كجم ← بروتين {fmt(p)} كجم" for f, q, p in res["lines"])
    return (
        f"{title}\n\n{rows}\n\n"
        f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم\n"
        f"🥩 كمية البروتين: {fmt(res['protein_kg'])} كجم\n"
        f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qty"] = fc.default_quantities(FEEDS)
    await update.message.reply_text(HELP)


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qty"] = fc.default_quantities(FEEDS)
    await update.message.reply_text("تمام، بدأنا من جديد ✨ ابعت الخامات والكميات.")


async def feeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الخامات المتاحة:\n" + "\n".join(f"• {f.name_ar}  |  {f.name_en}" for f in FEEDS))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = _qty(context)
    updates, errors = fc.parse_message(update.message.text, FEEDS)
    qty.update(updates)
    parts = []
    if updates:
        parts.append(summary_text(fc.compute(qty, FEEDS), "تمام سجّلت 👍 الخلطة الحالية:"))
    if errors:
        parts.append("⚠️ الأسطر دي مافهمتهاش:\n" + "\n".join(errors))
    if not parts:
        parts.append(HELP)
    await update.message.reply_text("\n\n".join(parts), reply_markup=KEYBOARD if updates else None)


async def _send_file(chat_message, context: ContextTypes.DEFAULT_TYPE):
    qty = _qty(context)
    res = fc.compute(qty, FEEDS)
    if res["total_weight"] <= 0:
        await chat_message.reply_text("لسه مدخلتش أي كميات. ابعت الخامات الأول.")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Feed_Calculation.xlsx")
        await asyncio.to_thread(fc.build_workbook, TEMPLATE, qty, path, RECALC)
        caption = (f"🥩 البروتين: {fmt(res['protein_kg'])} كجم\n"
                   f"📊 نسبة البروتين الخام: {res['cp_pct']:.2f}%\n"
                   f"⚖️ الوزن الكلي: {fmt(res['total_weight'])} كجم")
        with open(path, "rb") as fh:
            await chat_message.reply_document(fh, filename="Feed_Calculation.xlsx", caption=caption)


async def calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_file(update.message, context)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "calc":
        await _send_file(q.message, context)
    elif q.data == "reset":
        context.user_data["qty"] = fc.default_quantities(FEEDS)
        await q.message.reply_text("تمام، بدأنا من جديد ✨ ابعت الخامات والكميات.")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("حصل خطأ غير متوقع 😕 جرّب تاني بعد شوية.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط توكن البوت في متغير البيئة BOT_TOKEN (من @BotFather)")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
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
