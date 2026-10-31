"""اختبار مبدئي للهاندلرز بدون اتصال بتيليجرام."""
import asyncio, os
from unittest.mock import AsyncMock, MagicMock
os.environ["RECALC"] = "1"
import bot

async def main():
    ctx = MagicMock(); ctx.user_data = {}
    upd = MagicMock(); upd.message.text = "ذرة صفراء 500\nصويا 250\nقطن 250\nxyz 5"
    upd.message.reply_text = AsyncMock(); upd.message.reply_document = AsyncMock()
    await bot.on_text(upd, ctx)
    print(upd.message.reply_text.call_args[0][0]); print("-----")
    await bot.calc(upd, ctx)
    kw = upd.message.reply_document.call_args
    print("document sent:", kw.kwargs["filename"]); print(kw.kwargs["caption"])
asyncio.run(main())
