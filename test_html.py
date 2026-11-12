"""رسايل البوت لازم تكون HTML سليم لتيليجرام حتى مع مدخلات فيها < > & ، والبطاقة لازم تتولّد بأحجام مختلفة."""
import asyncio, io, os, tempfile
from unittest.mock import AsyncMock, MagicMock
os.environ["DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "d.json")
from PIL import Image
import bot, breed, card, storage
import feed_core as fc
from htmlcheck import validate_html

FEEDS = bot.FEEDS; by = {f.name_en: f for f in FEEDS}
NASTY = '<b>x</b> & "y" <script>'

def check(name, text):
    errs = validate_html(text)
    assert not errs, (name, errs, text[:200])

def mix(n):
    q = {f.row: 0.0 for f in FEEDS}
    for f in FEEDS[:n]: q[f.row] = 100.0 + f.row
    return q

# 1) الملخص بكل الأشكال (قصير/طويل، هدف ناقص/زايد/تمام، أسعار ناقصة، عنوان فيه رموز خاصة)
for n in (1, 3, 5, 12, 21):
    for target in (None, 10.0, 18.0, 40.0):
        for prices in ({}, {FEEDS[0].row: 12000.0}):
            res = fc.compute(mix(n), FEEDS, prices)
            t = bot.summary_text(res, NASTY, target); check(f"summary n={n} t={target}", t)
            assert "&lt;script&gt;" in t and "<script>" not in t
print("1) summary_text valid HTML for 40 combinations (incl. hostile title)")

# 2) باقي الشاشات
u = {"qty": mix(5), "prices": {}, "recipes": {NASTY: {FEEDS[0].row: 50.0}}, "target": 16.0}
for name, view in (("menu", bot.menu_view(u, True)), ("recipe", bot.recipe_view(u, NASTY)), ("recipes", bot.recipes_view(u)),
                   ("target", bot.target_view(u)), ("scale", bot.scale_view(u)), ("list", bot.list_view(u, 0)), ("truth", None)):
    if view: check(name, view[0])
for f in FEEDS: check("qty " + f.name_en, bot.qty_view(u, f.row)[0])
for lab in ["Angus", "Ayrshire", "BRAFORD", "BRANGUES NEGRO", "Brown Swiss", "Hereford", "Holstein_Friesian", "Jaffarabadi", "Jersey", "Red Dane", "Simmental", "Unknown <x>"]:
    check("breed " + lab, bot.rtl(breed.format_result([(lab, 0.8), ("Jersey", 0.1), ("Angus", 0.05)], 10)))
print("2) menu/recipes/target/scale/list/qty (21)/breed results: valid HTML")

# 3) المسارات اللي بتعكس نص من المستخدم أو من استثناء
async def paths():
    store = storage.Store(os.environ["DATA_FILE"], []); store.restored = True
    bd = {"store": store, "admin_ids": [1], "feeds": FEEDS}
    def ctx(uid):
        c = MagicMock(); c.application.bot_data = bd; c.user_data = {}; c.args = []; c.bot = MagicMock(); return c
    def upd(uid, text=None):
        x = MagicMock(); x.effective_user.id = uid; x.effective_user.full_name = NASTY; x.effective_user.username = "u"
        x.message.text = text; x.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock())); return x
    def sent(x): return x.message.reply_text.call_args.args[0]
    x = upd(5, f"{NASTY} 100"); await bot.on_message(x, ctx(5)); check("unknown line", sent(x)); assert "&lt;b&gt;" in sent(x)
    x = upd(5, "ذرة 5\n<i>سطر 7"); await bot.on_message(x, ctx(5)); check("mixed lines", sent(x))
    x = upd(5); await bot.feeds_cmd(x, ctx(5)); check("/feeds", sent(x)); assert "Vitamins &amp; Minerals" in sent(x)
    # recipe name hostile
    c = ctx(5); c.user_data["awaiting"] = {"type": "recipe"}; x = upd(5, NASTY); await bot.on_message(x, c); check("recipe saved", sent(x))
    # /model مع خطأ فيه رموز
    class FM:
        available, title, labels = False, "T", ["a"]
        error = "ValueError: <bad> & stuff"
    saved = bot.CLASSIFIERS; bot.CLASSIFIERS = {"domestic": FM(), "insight": FM()}
    c = ctx(1); c.args = ["domestic"]; x = upd(1); await bot.model_cmd(x, c); check("/model error", sent(x)); assert "&lt;bad&gt;" in sent(x)
    bot.CLASSIFIERS = saved
    # خطأ تحليل صورة للأدمن
    class Boom:
        available = True; labels = ["Angus"]
        def predict(self, d): raise ValueError("bad <image> & data")
    bot.CLASSIFIER = Boom(); c = ctx(1)
    x = upd(1); f = MagicMock(); f.download_as_bytearray = AsyncMock(return_value=bytearray(b"x")); ph = MagicMock(); ph.file_size = 10; ph.get_file = AsyncMock(return_value=f)
    x.message.photo = [ph]; x.message.document = None
    wait = MagicMock(); wait.edit_text = AsyncMock(); x.message.reply_text = AsyncMock(return_value=wait)
    await bot.on_photo(x, c); out = wait.edit_text.call_args.args[0]; check("photo error (admin)", out); assert "&lt;image&gt;" in out
    # حظر مستخدم اسمه فيه رموز
    import admin
    store.users[77] = {"name": NASTY, "banned": False}; c = ctx(1); c.args = ["77"]; x = upd(1); await admin.cmd_ban(x, c); check("ban reply", sent(x))
asyncio.run(paths())
print("3) hostile user text / exception text is escaped everywhere it is echoed")
assert len(bot.BRAND_SHORT) <= 120 and len(bot.BRAND_DESC) <= 512
print("4) bot description lengths within Telegram limits:", len(bot.BRAND_SHORT), len(bot.BRAND_DESC))

# 5) البطاقة: أحجام مختلفة
for n in (1, 3, 9, 14, 21):
    for target, prices in ((None, {}), (16.0, {f.row: 10000.0 for f in FEEDS})):
        res = fc.compute(mix(n), FEEDS, prices); st = fc.advice(res, target, FEEDS)[0] if target else None
        png = card.render_card(res, "feedbot_demo", target, st); im = Image.open(io.BytesIO(png))
        assert im.size[0] == 1080 and 700 < im.size[1] < 2400 and len(png) > 20_000, (n, im.size)
print("5) card renders for 1/3/9/14/21 ingredients, with/without target and prices")
res = fc.compute(mix(21), FEEDS, {f.row: 10000.0 for f in FEEDS}); open("/tmp/card21.png", "wb").write(card.render_card(res, "feedbot_demo", 16.0, fc.advice(res, 16.0, FEEDS)[0]))
print("ALL HTML/CARD TESTS OK")
