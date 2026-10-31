"""اختبار هاندلرز الأزرار بدون اتصال بتيليجرام."""
import asyncio, os
from unittest.mock import AsyncMock, MagicMock
import bot

def press(ctx, data):
    upd = MagicMock(); q = upd.callback_query
    q.data = data; q.answer = AsyncMock(); q.edit_message_text = AsyncMock()
    q.message.reply_text = AsyncMock(); q.message.reply_document = AsyncMock()
    return upd, q

async def main():
    ctx = MagicMock(); ctx.user_data = {}
    msg = MagicMock(); msg.reply_text = AsyncMock()
    start = MagicMock(); start.message = msg
    await bot.start(start, ctx)
    print("start ->", msg.reply_text.call_count, "messages")

    async def click(data):
        upd, q = press(ctx, data); await bot.on_button(upd, ctx); return q

    q = await click("a:0");  kb = q.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    print("list page 1:", [r[0].text for r in kb[:3]], "... buttons:", len(kb))
    q = await click("s:5")                                   # ذرة صفراء
    for d in ("d:5:500", "d:5:10", "d:5:-10"): q = await click(d)
    print("corn qty:", ctx.user_data["qty"][5])
    q = await click("s:7"); q = await click("t:7")
    assert ctx.user_data["awaiting"] == 7
    t = MagicMock(); t.message.text = "٢٥٠"; t.message.reply_text = AsyncMock()
    await bot.on_text(t, ctx)
    print("soy qty (typed ٢٥٠):", ctx.user_data["qty"][7], "| awaiting cleared:", "awaiting" not in ctx.user_data)
    t.message.text = "قطن 250"; await bot.on_text(t, ctx)   # الكتابة الحرة لسه شغالة
    q = await click("d:20:0"); print("bicarb zeroed:", ctx.user_data["qty"][20])
    q = await click("a:2"); print("last page:", q.edit_message_text.call_args.args[0])
    q = await click("calc")
    d = q.message.reply_document.call_args
    print("file:", d.kwargs["filename"]); print(d.kwargs["caption"])
    q = await click("reset"); print("reset ->", ctx.user_data["qty"][5])
asyncio.run(main())
