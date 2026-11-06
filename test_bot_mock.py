"""اختبار شامل للهاندلرز بدون اتصال بتيليجرام (محاكاة)."""
import asyncio, os, tempfile, json
from unittest.mock import AsyncMock, MagicMock
os.environ["ADMIN_IDS"] = "1"
os.environ["DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "bot_data.json")
import bot, admin, storage
from telegram.constants import ChatMemberStatus
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ApplicationHandlerStop

ADMIN, USER = 1, 100
bd = {"store": storage.Store(os.environ["DATA_FILE"], [ADMIN]), "admin_ids": [ADMIN], "feeds": bot.FEEDS}
bd["store"].restored = True

def ctx(uid):
    c = MagicMock(); c.application.bot_data = bd; c.bot = BOT
    c.user_data = USERDATA.setdefault(uid, {}); c.args = []
    return c
USERDATA = {}
BOT = MagicMock()
for n in ("send_message", "send_document", "copy_message", "pin_chat_message", "edit_message_media",
          "set_chat_menu_button", "delete_my_commands", "set_my_commands"):
    setattr(BOT, n, AsyncMock())

def upd_msg(uid, text=None):
    u = MagicMock(); u.effective_chat.id = uid; u.effective_user.id = uid; u.effective_user.full_name = f"user{uid}"; u.effective_user.username = f"u{uid}"
    u.effective_user.is_bot = False; u.callback_query = None
    m = u.message; m.text = text; m.chat_id = uid; m.message_id = 5
    m.reply_text = AsyncMock(); m.reply_document = AsyncMock()
    return u

def upd_btn(uid, data):
    u = MagicMock(); u.effective_user.id = uid; u.effective_user.full_name = f"user{uid}"; u.effective_user.username = f"u{uid}"
    u.effective_user.is_bot = False; u.message = None
    q = u.callback_query; q.data = data; q.answer = AsyncMock(); q.edit_message_text = AsyncMock()
    q.message.reply_text = AsyncMock(); q.message.reply_document = AsyncMock()
    return u

def member(status): return MagicMock(status=status)

async def press(uid, data):
    u = upd_btn(uid, data); c = ctx(uid)
    try: await bot.gate(u, c)
    except ApplicationHandlerStop: return u, "STOPPED"
    await bot.on_button(u, c); return u, None

async def send(uid, text):
    u = upd_msg(uid, text); c = ctx(uid)
    try: await bot.gate(u, c)
    except ApplicationHandlerStop: return u, "STOPPED"
    await bot.on_message(u, c); return u, None

def last_text(u):
    q = u.callback_query
    return q.edit_message_text.call_args.args[0] if q and q.edit_message_text.called else u.message.reply_text.call_args.args[0]

async def main():
    store = bd["store"]
    # ================= 1) بوابة الاشتراك =================
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.LEFT))
    u, r = await send(USER, "/start"); assert r == "STOPPED"
    assert "KIRA349A" in u.message.reply_text.call_args.args[0]
    kb = u.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert kb[0][0].url == "https://t.me/KIRA349A"; print("1a gate blocks non-subscriber: OK")
    u, r = await press(USER, "chk"); assert r == "STOPPED" and u.callback_query.answer.call_args.kwargs.get("show_alert")
    print("1b 'check' while not subscribed -> alert: OK")
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER))
    u, r = await press(USER, "chk"); assert r is None and "الخلطة" in last_text(u) or "لسه مفيش" in last_text(u)
    print("1c 'check' after subscribing -> menu: OK")
    BOT.get_chat_member = AsyncMock(side_effect=TelegramError("Chat not found"))
    bd["sub_cache"] = {}; u, r = await send(USER, "/new"); assert r is None
    assert BOT.send_message.call_count == 1 and BOT.send_message.call_args.args[0] == ADMIN
    await send(USER, "/new"); assert BOT.send_message.call_count == 1           # تنبيه الأدمن مرة واحدة بس
    print("1d API error -> fail-open + admin warned once: OK")
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.LEFT)); bd["sub_cache"] = {}
    u, r = await send(ADMIN, "/start"); assert r is None; print("1e admin bypasses gate: OK")
    store.users[USER]["banned"] = True
    u, r = await send(USER, "/new"); assert r == "STOPPED" and "حظر" in u.message.reply_text.call_args.args[0]
    store.users[USER]["banned"] = False; BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER)); print("1f banned user stopped: OK")

    # ================= 2) مسار الأزرار الكامل =================
    await send(USER, "/new")
    for d in ("a:0", "s:5", "d:5:500", "d:5:10", "d:5:-10", "pr:5"): u, _ = await press(USER, d)
    assert ctx(USER).user_data["awaiting"]["type"] == "price"
    u, _ = await send(USER, "١٢٠٠٠"); assert store.users[USER]["prices"][5] == 12000
    u, _ = await press(USER, "s:7"); u, _ = await press(USER, "t:7"); u, _ = await send(USER, "250")
    await press(USER, "s:8"); await press(USER, "d:8:100"); await press(USER, "d:8:100")
    u, _ = await press(USER, "tg:18"); t = last_text(u); assert "الهدف" in t and "💡" in t, t
    print("2a qty/price/typed-number/target:", store.users[USER]["qty"][5], store.users[USER]["qty"][7], store.users[USER]["qty"][8], store.users[USER]["target"])
    u, _ = await press(USER, "sc:1000"); total = sum(store.users[USER]["qty"].values()); assert abs(total - 1000) < 0.1
    print("2b scale to 1000: total =", round(total, 2))
    u, _ = await press(USER, "sv"); u, _ = await send(USER, "خلطة التسمين"); assert "خلطة التسمين" in store.users[USER]["recipes"]
    await press(USER, "reset"); assert store.users[USER]["qty"][5] == 0
    u, _ = await press(USER, "rc"); assert "1/20" in last_text(u)
    u, _ = await press(USER, "rl:خلطة التسمين"); assert "بروتين" in last_text(u)
    u, _ = await press(USER, "rg:خلطة التسمين"); assert store.users[USER]["qty"][5] > 0
    print("2c save -> reset -> load recipe: OK")
    u, _ = await press(USER, "calc"); d = u.callback_query.message.reply_document.call_args
    cap = d.kwargs["caption"]; assert "تكلفة" in cap and store.users[USER]["calcs"] == 1; print("2d file sent:", d.kwargs["filename"], "|", cap.replace("\n", " | "))
    u, _ = await press(USER, "rd:خلطة التسمين"); assert not store.users[USER]["recipes"]
    u, _ = await press(USER, "rl:مش موجودة"); assert "قديم" in u.callback_query.message.reply_text.call_args.args[0]
    u, _ = await press(USER, "zzz:1"); assert "قديم" in u.callback_query.message.reply_text.call_args.args[0]
    print("2e delete recipe + stale buttons handled: OK")
    u, _ = await press(USER, "adm:panel"); assert u.callback_query.answer.call_args.kwargs.get("show_alert"); print("2f non-admin blocked from admin buttons: OK")
    u, _ = await send(USER, "ذرة صفراء 300\nصويا 100"); assert store.users[USER]["qty"][5] == 300; print("2g free-text still works: OK")

    # ================= 3) الأدمن والبرودكاست =================
    for i in (201, 202, 203, 204): store.user(i, {}); store.users[i]["name"] = f"u{i}"
    store.users[205] = {**store.users[201], "banned": True}
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER))
    u, _ = await press(ADMIN, "adm:panel"); t = last_text(u); assert "لوحة الأدمن" in t; print("3a panel:", t.splitlines()[2], "|", t.splitlines()[-1])
    u, _ = await press(ADMIN, "adm:bc"); assert ctx(ADMIN).user_data["awaiting"]["type"] == "bc"
    u = upd_msg(ADMIN, "📢 إعلان مهم للجميع"); c = ctx(ADMIN); await bot.on_message(u, c)
    assert BOT.copy_message.call_count == 1 and "معاينة" in u.message.reply_text.call_args.args[0]
    BOT.copy_message.reset_mock()
    calls = {"n": 0}
    async def fake_copy(chat_id, from_chat_id, message_id):
        if chat_id == 202: raise Forbidden("bot was blocked by the user")
        if chat_id == 203 and calls["n"] == 0: calls["n"] += 1; raise RetryAfter(0)
        if chat_id == 204: raise TelegramError("boom")
    BOT.copy_message = AsyncMock(side_effect=fake_copy)
    admin.SEND_DELAY = 0
    app = MagicMock(); app.bot = BOT; app.bot_data = bd
    status = MagicMock(); status.edit_text = AsyncMock()
    await admin.run_broadcast(app, (ADMIN, 9), status)
    rep = status.edit_text.call_args.args[0]; print("3b broadcast report:", rep.replace("\n", " | "))
    sent_to = sorted(c.kwargs["chat_id"] for c in BOT.copy_message.call_args_list)
    assert 205 not in sent_to and store.users[202]["blocked"] is True and "وصل: 4" in rep  # ADMIN, USER, 201, 203(بعد retry)
    assert 202 not in store.broadcast_targets()
    await send(ADMIN, "/ban"); c = ctx(ADMIN); c.args = ["203"]; u = upd_msg(ADMIN); await admin.cmd_ban(u, c); assert store.users[203]["banned"]
    c.args = ["1"]; await admin.cmd_ban(u, c); assert "أدمن" in u.message.reply_text.call_args.args[0]
    c.args = ["203"]; await admin.cmd_unban(u, c); assert not store.users[203]["banned"]
    u = upd_msg(ADMIN); await admin.cmd_users(u, ctx(ADMIN)); csv_bytes = u.message.reply_document.call_args.kwargs["document"]
    assert b"id,name" in csv_bytes and len(csv_bytes.splitlines()) >= 6; print("3c ban/unban/users.csv: OK")

    # ================= 3d) تنضيف الزراير اللي تحت + قائمة الأوامر =================
    from telegram import ReplyKeyboardRemove, MenuButtonCommands
    BOT.set_chat_menu_button.reset_mock()
    u = upd_msg(USER, "/start"); await bot.start(u, ctx(USER))
    first = u.message.reply_text.call_args_list[0]
    assert isinstance(first.kwargs["reply_markup"], ReplyKeyboardRemove)             # كيبورد قديم يتشال
    assert u.message.reply_text.call_args_list[1].kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "a:0"   # القائمة الرئيسية بزراير جوه الرسالة
    assert BOT.set_chat_menu_button.call_args.kwargs["chat_id"] == USER and isinstance(BOT.set_chat_menu_button.call_args.kwargs["menu_button"], MenuButtonCommands)
    print("3d /start removes stale reply keyboard + resets chat menu button: OK")
    for n in ("delete_my_commands", "set_my_commands", "set_chat_menu_button"): getattr(BOT, n).reset_mock()
    app = MagicMock(); app.bot = BOT; app.bot_data = bd
    await bot.reset_menus(app)
    scopes = {type(c.kwargs["scope"]).__name__ for c in BOT.delete_my_commands.call_args_list}
    assert scopes == {"BotCommandScopeDefault", "BotCommandScopeAllPrivateChats", "BotCommandScopeChat"} and BOT.delete_my_commands.call_count == 9
    shown = [c.name if hasattr(c, "name") else c.command for c in BOT.set_my_commands.call_args_list[0].args[0]]
    adm_shown = [c.command for c in BOT.set_my_commands.call_args_list[1].args[0]]
    assert shown == ["start", "calc", "new", "help"] and "broadcast" in adm_shown and "import" in adm_shown
    print("3e menus reset (all scopes/langs cleared) ->", shown, "| admin:", adm_shown)
    bot.COMMAND_MENU = False; BOT.set_my_commands.reset_mock(); await bot.reset_menus(app)
    assert not BOT.set_my_commands.called; bot.COMMAND_MENU = True; print("3f COMMAND_MENU=0 removes the menu entirely: OK")

    # ================= 3g) استيراد المستخدمين القدام =================
    OLD = """5464602559
5063876517
6808448615
8298826687 8385259502, 1922421109
5464602559
12"""
    assert admin.parse_ids(OLD) == [5464602559, 5063876517, 6808448615, 8298826687, 8385259502, 1922421109]   # مكرر/قصير بيتشال
    u, _ = await press(ADMIN, "adm:imp"); assert ctx(ADMIN).user_data["awaiting"]["type"] == "import"
    u = upd_msg(ADMIN, OLD); u.message.document = None; c = ctx(ADMIN); await bot.on_message(u, c)
    assert "اتضاف 6" in u.message.reply_text.call_args.args[0] and "awaiting" not in c.user_data
    assert store.users[5464602559]["imported"] and store.users[5464602559]["last_seen"] == 0
    st = store.stats(); assert st["imported_pending"] == 6; print("3g import from pasted text: added 6 (dupes/short ignored), pending =", st["imported_pending"])
    c = ctx(ADMIN); c.args = ["5464602559", "7777777777"]; u = upd_msg(ADMIN); await admin.cmd_import(u, c)
    assert "اتضاف 1" in u.message.reply_text.call_args.args[0] and "1 كانوا موجودين" in u.message.reply_text.call_args.args[0]
    # استيراد من ملف
    u = upd_msg(ADMIN, None); u.message.document.file_size = 100
    f = MagicMock(); f.download_as_bytearray = AsyncMock(return_value=bytearray("id\n9999999991\n9999999992\n".encode())); u.message.document.get_file = AsyncMock(return_value=f)
    ctx(ADMIN).user_data["awaiting"] = {"type": "import"}; await bot.on_message(u, ctx(ADMIN))
    assert "اتضاف 2" in u.message.reply_text.call_args.args[0]; print("3h import via /import args and via file: OK")
    ctx(ADMIN).user_data["awaiting"] = {"type": "import"}; u = upd_msg(ADMIN, "مفيش أرقام"); u.message.document = None; await bot.on_message(u, ctx(ADMIN))
    assert "مالقيتش" in u.message.reply_text.call_args.args[0] and ctx(ADMIN).user_data.get("awaiting")
    assert 5464602559 in store.broadcast_targets(); print("3i bad import input keeps waiting; imported users are broadcast targets: OK")
    u, _ = await press(ADMIN, "adm:panel"); assert "مستوردين" in last_text(u)
    ctx(ADMIN).user_data.pop("awaiting", None)
    # أول رسالة من مستورَد بتعدّيه لمستخدم نشط
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER)); bd["sub_cache"] = {}
    await send(5464602559, "/new"); assert store.users[5464602559]["last_seen"] > 0 and store.stats()["imported_pending"] == 8
    print("3j imported user becomes active after first message: pending now", store.stats()["imported_pending"])

    # ================= 3k) تتبع المصدر + المشاركة + اللينكات =================
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER)); bd["sub_cache"] = {}
    for uid, text in ((300, "/start fb"), (301, "/start yt_campaign1"), (302, "/start bad payload!"), (303, "/start")):
        await bot.gate(upd_msg(uid, text), ctx(uid))
    await bot.gate(upd_msg(300, "/start tt"), ctx(300))                           # ما يغيّرش المصدر الأصلي
    src = {u: store.users[u]["source"] for u in (300, 301, 302, 303, 5464602559)}
    assert src == {300: "fb", 301: "yt_campaign1", 302: "organic", 303: "organic", 5464602559: "imported"}, src
    u, _ = await press(ADMIN, "adm:panel"); line = [l for l in last_text(u).splitlines() if "المصادر" in l][0]
    assert "fb 1" in line; print("3k source tracking:", src, "|", line)
    bot.BOT_USERNAME = "feedbot_test"; bd["username"] = "feedbot_test"
    t, kb = bot.menu_view(store.users[USER], False); urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert len(urls) == 1 and urls[0].startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2Ffeedbot_test%3Fstart%3Dshare"), urls
    c = ctx(ADMIN); u = upd_msg(ADMIN); await admin.cmd_links(u, c); txt = u.message.reply_text.call_args.args[0]
    assert "https://t.me/feedbot_test?start=fb" in txt and "?start=wa" in txt
    u, _ = await press(USER, "calc"); cap = u.callback_query.message.reply_document.call_args.kwargs["caption"]; assert "@feedbot_test" in cap
    print("3l share button url + /links + caption footer: OK")
    bot.BOT_USERNAME = ""; bot.WATERMARK = True

    # ================= 3m) التعرّف على السلالة (بموديل مزيّف) =================
    BOT.get_chat_member = AsyncMock(return_value=member(ChatMemberStatus.MEMBER)); bd["sub_cache"] = {}
    t, kb = bot.menu_view(store.users[USER], False)
    assert any(b.callback_data == "br" for row in kb.inline_keyboard for b in row); print("3m menu has breed button: OK")
    class Fake:
        available = True; labels = ["Angus", "Jersey", "Simmental"]; calls = 0; raise_exc = False
        def predict(self, data):
            Fake.calls += 1
            if Fake.raise_exc: raise ValueError("bad image")
            return [("Jersey", 0.62), ("Angus", 0.3), ("Simmental", 0.08)]
    def photo_update(uid, size=1000, doc=False):
        u = upd_msg(uid, None); u.effective_user.is_bot = False
        f = MagicMock(); f.download_as_bytearray = AsyncMock(return_value=bytearray(b"img"))
        big = MagicMock(); big.file_size = size; big.get_file = AsyncMock(return_value=f)
        u.message.photo = [] if doc else [MagicMock(), big]; u.message.document = big if doc else None
        wait = MagicMock(); wait.edit_text = AsyncMock(); u.message.reply_text = AsyncMock(return_value=wait); u.wait = wait
        return u
    async def photo(uid, **kw):
        u = photo_update(uid, **kw); c = ctx(uid)
        try: await bot.gate(u, c)
        except ApplicationHandlerStop: return u, "STOPPED"
        await bot.on_photo(u, c); return u, None
    bot.CLASSIFIER = Fake()
    u, _ = await photo(USER); out = u.wait.edit_text.call_args.args[0]
    assert "جيرسي" in out and "62.0%" in out and "توقع تقريبي" in out and store.users[USER]["breed_scans"] == 1
    print("3n photo -> breed result:", out.splitlines()[2:5])
    # ---- 3t) تقييم النتيجة + إرسال الصورة للتحسين بموافقة المستخدم
    BOT.send_photo = AsyncMock()
    u, _ = await press(USER, "fb:n"); assert "السلالة الصح" in last_text(u)
    kb = u.callback_query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert len([b for row in kb for b in row]) == 3 + 1                         # 3 سلالات في الموديل المزيّف + «سلالة تانية»
    u, _ = await press(USER, "fb:t0")                                           # الموديل قال Jersey والمستخدم قال Angus
    assert store.users[USER].get("fb_bad", 0) == 1 and "تحب تبعت" in last_text(u)
    u, _ = await press(USER, "fb:y"); assert store.users[USER].get("fb_ok", 0) == 0 and store.users[USER]["fb_bad"] == 1   # نفس الصورة ما تتحسبش مرتين
    assert BOT.send_photo.call_count == 0                                       # من غير موافقة مفيش صورة بتتبعت
    u, _ = await press(USER, "fb:s"); c = BOT.send_photo.call_args
    assert c.args[0] == ADMIN and "truth=Angus" in c.kwargs["caption"] and "pred=Jersey" in c.kwargs["caption"] and "@" not in c.kwargs["caption"]
    u, _ = await press(USER, "fb:s"); assert BOT.send_photo.call_count == 1 and store.users[USER]["fb_shared"] == 1
    print("3t rating ❌ + pick true breed + consent -> photo to admin with labels only (no identity), once: OK")
    ctx(USER).user_data["last_breed"] = 0; await photo(USER); u, _ = await press(USER, "fb:y"); assert store.users[USER]["fb_ok"] == 1
    u, _ = await press(USER, "fb:x"); assert "شكراً" in last_text(u) and BOT.send_photo.call_count == 1
    u, _ = await press(300, "fb:y"); assert "قديم" in u.callback_query.message.reply_text.call_args.args[0]; print("3u correct rating counted, decline sends nothing, stale rating button handled: OK")
    u, _ = await press(ADMIN, "adm:panel"); assert "✅ 1 • ❌ 1" in last_text(u); print("3v admin panel:", [l for l in last_text(u).splitlines() if "تقييمات" in l or "اتبعتت" in l])
    # ---- 3w) أمر /model: عرض، تبديل، رفض اسم غلط/موديل واقف، fallback، إحصائيات لكل موديل
    saved = (bot.CLASSIFIERS, bot.CLASSIFIER, bot.ACTIVE_NAME)
    class FM:
        def __init__(s, av, title): s.available, s.title, s.error, s.labels = av, title, (None if av else "boom"), ["Angus", "Jersey", "Simmental"]
        def predict(s, data): return [("Jersey", 0.6), ("Angus", 0.3), ("Simmental", 0.1)]
    bot.CLASSIFIERS = {"domestic": FM(True, "D"), "insight": FM(True, "I")}
    appm = MagicMock(); appm.bot_data = bd
    async def model_cmd(uid, *args):
        c = ctx(uid); c.args = list(args); u = upd_msg(uid); await bot.model_cmd(u, c); return u
    assert bot.set_active(appm, "domestic") == "domestic"
    u = await model_cmd(ADMIN); t = u.message.reply_text.call_args.args[0]; assert "▶️ domestic" in t and "▫️ insight" in t
    u = await model_cmd(ADMIN, "insight"); assert store.settings["breed_model"] == "insight" and bot.CLASSIFIER is bot.CLASSIFIERS["insight"] and "▶️ insight" in u.message.reply_text.call_args.args[0]
    assert "insight" in bd["breed_active_title"]
    u = await model_cmd(ADMIN, "nope"); assert "مش معروف" in u.message.reply_text.call_args.args[0]
    bot.CLASSIFIERS["domestic"].available = False; bot.CLASSIFIERS["domestic"].error = "weights missing"
    u = await model_cmd(ADMIN, "domestic"); assert "مش شغّال" in u.message.reply_text.call_args.args[0] and bot.ACTIVE_NAME == "insight"
    u = await model_cmd(USER, "domestic"); assert not u.message.reply_text.called                      # مستخدم عادي: تجاهل
    bot.CLASSIFIERS["insight"].available = False; assert bot.set_active(appm, "insight") is None and "مفيش" in bd["breed_active_title"]
    bot.CLASSIFIERS["insight"].available = True; bot.CLASSIFIERS["domestic"].available = True
    bot.CLASSIFIERS["domestic"].available = False; assert bot.set_active(appm, "domestic") == "insight"; bot.CLASSIFIERS["domestic"].available = True   # fallback للشغّال
    bot.set_active(appm, "insight"); ctx(USER).user_data["last_breed"] = 0; await photo(USER); await press(USER, "fb:y")
    assert store.users[USER]["fb_models"]["insight"] == {"ok": 1, "bad": 0} and store.users[USER]["fb_models"]["domestic"] == {"ok": 1, "bad": 1}
    u, _ = await press(ADMIN, "adm:panel"); assert "insight: ✅ 1 • ❌ 0" in last_text(u) and "موديل السلالة النشط: insight" in last_text(u)
    print("3w /model: show/switch/reject unknown/reject dead model/non-admin ignored/fallback + per-model feedback stats: OK")
    bot.CLASSIFIERS, bot.CLASSIFIER, bot.ACTIVE_NAME = saved
    ctx(USER).user_data["last_breed"] = 0
    u, _ = await photo(USER); u, _ = await photo(USER); assert "استنى" in u.message.reply_text.call_args.args[0] and Fake.calls == 3; print("3o cooldown blocks rapid second photo: OK")
    ctx(USER).user_data["last_breed"] = 0; u, _ = await photo(USER, doc=True); assert Fake.calls == 4; print("3p image sent as document works: OK")
    ctx(USER).user_data["last_breed"] = 0; u, _ = await photo(USER, size=20_000_000); assert "كبيرة" in u.message.reply_text.call_args.args[0] and Fake.calls == 4
    ctx(USER).user_data["last_breed"] = 0; Fake.raise_exc = True; u, _ = await photo(USER); assert "مقدرتش" in u.wait.edit_text.call_args.args[0]; Fake.raise_exc = False
    print("3q oversize + decode failure handled: OK")
    bot.CLASSIFIER = MagicMock(available=False)
    ctx(USER).user_data["last_breed"] = 0; u, _ = await photo(USER); assert "مش متاحة" in u.message.reply_text.call_args.args[0]
    u, _ = await press(USER, "br"); assert "مش متاحة" in last_text(u)
    bot.CLASSIFIER = Fake(); u, _ = await press(USER, "br"); assert "ابعتلي صورة" in last_text(u); print("3r unavailable model -> polite message; prompt screen OK")
    # الأدمن بيبعت صورة للبرودكاست: لازم تروح للبرودكاست مش للتعرّف
    ctx(ADMIN).user_data["awaiting"] = {"type": "bc"}; calls_before = Fake.calls
    u = photo_update(ADMIN); BOT.copy_message = AsyncMock(); await bot.on_photo(u, ctx(ADMIN))
    assert Fake.calls == calls_before and ctx(ADMIN).user_data.get("bc_src"); print("3s admin photo during broadcast goes to broadcast: OK")
    st = store.stats(); assert st["scans_total"] == 5, st["scans_total"]; u, _ = await press(ADMIN, "adm:panel"); assert "عمليات تعرّف السلالة: 5" in last_text(u)
    bot.CLASSIFIER = MagicMock(available=False)

    # ================= 4) الحفظ بعد إعادة التشغيل =================
    store.mark(); await store.flush(BOT, force=True)                       # محلي + نسخة تيليجرام
    assert BOT.send_document.called and BOT.pin_chat_message.called
    payload = BOT.send_document.call_args.kwargs["document"]; fn = BOT.send_document.call_args.kwargs["filename"]
    print("4a backup sent+pinned:", fn, len(payload), "bytes")
    store.users[USER]["qty"][5] = 123; store.mark(); BOT.edit_message_media = AsyncMock(); store._last_backup = 0
    await store.flush(BOT); assert BOT.edit_message_media.called; print("4b next backup edits the same message: OK")
    # إعادة تشغيل = مخزن جديد من غير ملف محلي، يستعيد من تيليجرام
    fresh = storage.Store(os.path.join(tempfile.mkdtemp(), "none.json"), [ADMIN]); fresh.restored = False
    pm = MagicMock(); pm.message_id = 77; pm.document.file_name = "bot_data.json"
    f = MagicMock(); f.download_as_bytearray = AsyncMock(return_value=bytearray(payload)); pm.document.get_file = AsyncMock(return_value=f)
    B2 = MagicMock(); B2.get_chat = AsyncMock(return_value=MagicMock(pinned_message=pm))
    assert await fresh.restore_from_telegram(B2)
    assert len(fresh.users) == len(store.users) and fresh.users[USER]["recipes"] == {} and fresh.users[USER]["prices"][5] == 12000
    assert fresh.backup_msg == 77 and isinstance(list(fresh.users[USER]["qty"])[0], int)
    print("4c restored", len(fresh.users), "users from Telegram backup (prices/qty keys are ints): OK")
    B3 = MagicMock(); B3.get_chat = AsyncMock(side_effect=TelegramError("network")); fresh2 = storage.Store(os.path.join(tempfile.mkdtemp(), "x.json"), [ADMIN]); fresh2.restored = False
    assert not await fresh2.restore_from_telegram(B3); fresh2.dirty = True; B3.send_document = AsyncMock()
    await fresh2.flush(B3, force=True); assert not B3.send_document.called
    print("4d restore failure never overwrites the backup: OK")
    local = storage.Store(os.environ["DATA_FILE"], []); assert local.load_local() and USER in local.users; print("4e local JSON reload: OK")
    print("\nALL BOT TESTS OK")
asyncio.run(main())
