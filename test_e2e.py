"""
اختبار شامل: بيشغّل البوت الحقيقي (python bot.py) كعملية مستقلة ضد سيرفر محلي بيحاكي Telegram Bot API.
بيغطي: التشغيل، البوابة، الأزرار، ملف الإكسل، الأدمن والبرودكاست، التعرّف على السلالة،
        الإيقاف (SIGTERM) وحفظ النسخة الاحتياطية المثبّتة، وإعادة التشغيل واستعادتها.

    [CATTLE_MODEL_DIR=/path/to/models] python test_e2e.py
ملحوظة: ده محاكي لتيليجرام مبني على توثيق الـ Bot API، مش تيليجرام الحقيقي.
"""
import io, json, os, re, signal, subprocess, sys, tempfile, threading, time, urllib.parse
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "111111:E2E-TEST"
BOT = {"id": 111111, "is_bot": True, "first_name": "FeedBot", "username": "feedbot_e2e"}
ADMIN, USER, NOSUB = 1, 100, 200
HERE = os.path.dirname(os.path.abspath(__file__))
JSON_KEYS = {"reply_markup", "media", "commands", "scope", "menu_button", "allowed_updates"}


class FakeTelegram:
    def __init__(self):
        self.cv = threading.Condition(); self.updates = []; self.uid = 1; self.mid = 1000
        self.calls = []; self.files = {}; self.msgs = {}; self.pinned = {}; self.last = {}
        self.members = {NOSUB: "left"}; self.blocked = set()

    # ---------- مساعدين ----------
    def next_mid(self):
        self.mid += 1; return self.mid

    def message(self, chat_id, text=None, **extra):
        m = {"message_id": self.next_mid(), "date": int(time.time()), "chat": {"id": chat_id, "type": "private", "first_name": f"U{chat_id}"},
             "from": BOT}
        if text is not None: m["text"] = text
        m.update(extra); self.msgs[m["message_id"]] = m; self.last[chat_id] = m["message_id"]; return m

    def store_file(self, name, data):
        fid = f"F{len(self.files) + 1}"; self.files[fid] = (name, data); return fid

    def doc_fields(self, fid):
        name, data = self.files[fid]
        return {"file_id": fid, "file_unique_id": fid, "file_name": name, "file_size": len(data)}

    # ---------- حقن تحديثات من المستخدمين ----------
    def inject(self, upd):
        with self.cv:
            upd["update_id"] = self.uid; self.uid += 1; self.updates.append(upd); self.cv.notify_all()

    @staticmethod
    def user(uid): return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"u{uid}", "language_code": "ar"}

    def user_msg(self, uid, **fields):
        m = {"message_id": self.next_mid(), "date": int(time.time()), "chat": {"id": uid, "type": "private", "first_name": f"U{uid}"}, "from": self.user(uid)}
        m.update(fields); return m

    def say(self, uid, text):
        extra = {}
        if text.startswith("/"):
            extra["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        self.inject({"message": self.user_msg(uid, text=text, **extra)})

    def press(self, uid, data):
        mid = self.last.get(uid) or self.next_mid()
        self.inject({"callback_query": {"id": str(self.next_mid()), "from": self.user(uid), "chat_instance": "x", "data": data,
                     "message": {"message_id": mid, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "text": "menu"}}})

    def photo(self, uid, jpeg: bytes, doc=False):
        fid = self.store_file("photo.jpg", jpeg)
        if doc:
            self.inject({"message": self.user_msg(uid, document={**self.doc_fields(fid), "mime_type": "image/jpeg"})})
        else:
            sizes = [{"file_id": fid, "file_unique_id": fid, "width": 90, "height": 60, "file_size": len(jpeg)},
                     {"file_id": fid, "file_unique_id": fid, "width": 640, "height": 480, "file_size": len(jpeg)}]
            self.inject({"message": self.user_msg(uid, photo=sizes)})

    # ---------- انتظار ردود البوت ----------
    def mark(self): return len(self.calls)

    def wait(self, method, since=0, timeout=30, where=lambda p, f: True):
        end = time.time() + timeout
        while time.time() < end:
            for i in range(since, len(self.calls)):
                m, p, f = self.calls[i]
                if m == method and where(p, f): return i, p, f
            time.sleep(0.1)
        raise TimeoutError(f"no {method} after {timeout}s; calls since mark: {[c[0] for c in self.calls[since:]][-12:]}")


FAKE = FakeTelegram()


def parse_body(handler, raw):
    ct = handler.headers.get("Content-Type", "")
    params, files = {}, {}
    if "application/json" in ct:
        params = json.loads(raw or b"{}")
    elif "multipart/form-data" in ct:
        msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ct.encode() + b"\r\n\r\n" + raw)
        for part in msg.iter_parts():
            name, fn, data = part.get_param("name", header="content-disposition"), part.get_filename(), part.get_payload(decode=True)
            if fn is not None: files[name] = (fn, data)
            else: params[name] = data.decode("utf-8")
    else:
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
    for k in ("chat_id", "user_id", "message_id", "from_chat_id", "offset", "timeout"):      # form-data بيبعتهم نص
        if isinstance(params.get(k), str) and re.fullmatch(r"-?\d+", params[k]): params[k] = int(params[k])
    for k in JSON_KEYS & set(params):
        if isinstance(params[k], str) and params[k][:1] in "{[":
            params[k] = json.loads(params[k])
    return params, files


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address): pass          # قطع الاتصال عند إيقاف البوت مش خطأ


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def reply(self, obj, status=200, raw=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        m = re.match(rf"/file/bot{re.escape(TOKEN)}/docs/(\w+)", urllib.parse.unquote(self.path))
        if m and m.group(1) in FAKE.files: return self.reply(None, raw=FAKE.files[m.group(1)][1])
        self.reply({"ok": False, "description": "not found"}, 404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        m = re.match(rf"/bot{re.escape(TOKEN)}/(\w+)", self.path)
        if not m: return self.reply({"ok": False, "description": "bad path"}, 404)
        method = m.group(1); params, files = parse_body(self, raw)
        if method != "getUpdates": FAKE.calls.append((method, params, files))
        try:
            result = self.dispatch(method, params, files)
        except PermissionError as e:
            return self.reply({"ok": False, "error_code": 403, "description": str(e)}, 403)
        self.reply({"ok": True, "result": result})

    def dispatch(self, method, p, files):
        F = FAKE; cid = int(p["chat_id"]) if str(p.get("chat_id", "")).lstrip("-").isdigit() else None
        if method == "getMe": return BOT
        if method == "getUpdates":
            offset, end = int(p.get("offset", 0) or 0), time.time() + min(int(p.get("timeout", 0) or 0), 2)
            with F.cv:
                while True:
                    F.updates = [u for u in F.updates if u["update_id"] >= offset] if offset else F.updates
                    if F.updates or time.time() >= end: return list(F.updates)
                    F.cv.wait(0.2)
        if method == "sendMessage":              # تيليجرام الحقيقي بيرجّع الكيبورد الداخلي بس (مش remove_keyboard)
            rm = p.get("reply_markup")
            return F.message(cid, p["text"], **({"reply_markup": rm} if isinstance(rm, dict) and "inline_keyboard" in rm else {}))
        if method == "editMessageText":
            m = F.msgs.get(int(p["message_id"])) or F.message(cid); m["text"] = p["text"]; return m
        if method == "sendDocument":
            name, data = files["document"]; fid = F.store_file(name, data)
            return F.message(cid, document=F.doc_fields(fid), caption=p.get("caption", ""))
        if method == "editMessageMedia":
            att = p["media"]["media"].replace("attach://", ""); name, data = files[att]
            m = F.msgs[int(p["message_id"])]; fid = F.store_file(name, data); m["document"] = F.doc_fields(fid); return m
        if method == "getChatMember":
            return {"status": F.members.get(int(p["user_id"]), "member"), "user": F.user(int(p["user_id"]))}
        if method == "getChat":
            c = {"id": cid, "type": "private", "first_name": f"U{cid}", "accent_color_id": 0, "max_reaction_count": 11}   # ChatFullInfo
            if cid in F.pinned: c["pinned_message"] = F.msgs[F.pinned[cid]]
            return c
        if method == "pinChatMessage": F.pinned[cid] = int(p["message_id"]); return True
        if method == "getFile":
            fid = p["file_id"]; return {"file_id": fid, "file_unique_id": fid, "file_size": len(F.files[fid][1]), "file_path": f"docs/{fid}"}
        if method == "copyMessage":
            if cid in F.blocked: raise PermissionError("Forbidden: bot was blocked by the user")
            return {"message_id": F.next_mid()}
        if method == "sendPhoto":
            return F.message(cid, photo=[{"file_id": "P1", "file_unique_id": "P1", "width": 1, "height": 1}], caption=p.get("caption", ""))
        return True        # answerCallbackQuery, setMyCommands, deleteMyCommands, setChatMenuButton, deleteWebhook ...


# ======================= السيناريو =======================
def start_bot(port, data_file):
    env = dict(os.environ, BOT_TOKEN=TOKEN, ADMIN_IDS=str(ADMIN), TELEGRAM_API_URL=f"http://127.0.0.1:{port}",
               DATA_FILE=data_file, TF_CPP_MIN_LOG_LEVEL="3", PYTHONUNBUFFERED="1")
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    return subprocess.Popen([os.environ.get("BOT_PYTHON", sys.executable), os.path.join(HERE, "bot.py")], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=HERE), log


def stop_bot(proc):
    proc.send_signal(signal.SIGTERM)
    try: return proc.wait(timeout=30)
    except subprocess.TimeoutExpired: proc.kill(); return "killed"


def ok(msg): print("  ✔", msg, flush=True)


def main():
    srv = QuietServer(("127.0.0.1", 0), Handler); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = tempfile.mkdtemp(); F = FAKE; proc = log = None
    try:
        print("== تشغيل 1 ==")
        proc, log = start_bot(port, os.path.join(tmp, "run1.json"))
        F.wait("setChatMenuButton", 0, 60); ok("البوت اشتغل وبدأ polling، ونضّف قوايم الأوامر وزرار Menu")
        deleted = [c for c in F.calls if c[0] == "deleteMyCommands"]; setc = [c for c in F.calls if c[0] == "setMyCommands"]
        assert len(deleted) == 9 and [x["command"] for x in setc[0][1]["commands"]] == ["start", "calc", "new", "help"]
        assert any(x["command"] == "broadcast" for x in setc[1][1]["commands"]); ok("قايمة الأوامر: 4 للمستخدم + أوامر الأدمن لحسابه بس")

        m = F.mark(); F.say(USER, "/start fb")
        _, p, _ = F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == USER and "reply_markup" in p and "remove_keyboard" in json.dumps(p["reply_markup"]))
        _, menu, _ = F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == USER and "inline_keyboard" in json.dumps(p.get("reply_markup", {})))
        kb = [b.get("callback_data") for row in menu["reply_markup"]["inline_keyboard"] for b in row]
        assert "a:0" in kb and "calc" in kb and "br" in kb and any(b.get("url", "").startswith("https://t.me/share/url") for row in menu["reply_markup"]["inline_keyboard"] for b in row)
        F.wait("setChatMenuButton", m, 10, lambda p, f: p.get("chat_id") == USER); ok("/start: ReplyKeyboardRemove + القائمة بالأزرار (شامل «شارك البوت» و«السلالة»)")

        m = F.mark(); F.say(NOSUB, "/start"); _, p, _ = F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == NOSUB)
        assert "KIRA349A" in p["text"] and "t.me/KIRA349A" in json.dumps(p["reply_markup"]); ok("غير المشترك في القناة: بيتمنع وبيظهرله زرار الاشتراك")
        m = F.mark(); F.members[NOSUB] = "member"; F.press(NOSUB, "chk"); F.wait("editMessageText", m, 20, lambda p, f: "chat_id" in p or True); ok("بعد الاشتراك زرار «تحقق» بيفتح القائمة")

        m = F.mark(); F.press(USER, "a:0"); _, p, _ = F.wait("editMessageText", m, 20); assert "اختار الخامة" in p["text"]
        for d in ("s:5", "d:5:500", "d:5:100"):
            m = F.mark(); F.press(USER, d); F.wait("editMessageText", m, 20)
        assert "600.00 كجم" in F.msgs[F.last[USER]]["text"]; ok("اختيار خامة وزيادة الكمية بالأزرار (600 كجم ذرة)")
        m = F.mark(); F.press(USER, "m"); F.wait("editMessageText", m, 20)
        m = F.mark(); F.press(USER, "calc"); _, p, files = F.wait("sendDocument", m, 90, lambda p, f: p["chat_id"] == USER)
        name, data = files["document"]
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
        assert name == "Feed_Calculation.xlsx" and ws["A5"].value.startswith("ذرة صفراء") and abs(ws["B5"].value - 600) < 1e-9
        n = 7                                                        # ذرة + 6 إضافات بجرعاتها القياسية
        assert abs(ws.cell(5 + n, 9).value - 1) < 1e-9 and abs(ws.cell(5 + n, 2).value - 630) < 1e-9, (ws.cell(5 + n, 9).value, ws.cell(5 + n, 2).value)
        assert "feedbot_e2e" in (ws.cell(7 + n, 1).value or ""), ws.cell(7 + n, 1).value
        assert "@feedbot_e2e" in p["caption"]
        ok(f"/calc: وصل ملف إكسل ({len(data)//1024} KB): الخامات المختارة بس، مجموع النسب 100%، رابط البوت تحت الجدول")

        print("== الأدمن والبرودكاست ==")
        m = F.mark(); F.say(ADMIN, "/start"); F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == ADMIN and "لوحة الأدمن" in json.dumps(p.get("reply_markup", {}), ensure_ascii=False))
        for uid in (5464602559, 5063876517): F.blocked.add(uid)
        m = F.mark(); F.say(ADMIN, "/import 5464602559 5063876517 6808448615")
        _, p, _ = F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == ADMIN and "اتضاف" in p["text"]); assert "اتضاف 3" in p["text"]; ok("/import: " + p["text"].splitlines()[0].replace("\u200f", ""))
        m = F.mark(); F.say(USER, "/admin"); time.sleep(1.5); assert not [c for c in F.calls[m:] if c[0] == "sendMessage" and c[1]["chat_id"] == USER and "لوحة الأدمن" in c[1]["text"]]; ok("مستخدم عادي مش بيشوف لوحة الأدمن")
        m = F.mark(); F.say(ADMIN, "/broadcast"); F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == ADMIN and "ابعت" in p["text"])
        m = F.mark(); F.say(ADMIN, "📢 إعلان تجريبي"); F.wait("copyMessage", m, 20, lambda p, f: int(p["chat_id"]) == ADMIN)
        F.wait("sendMessage", m, 20, lambda p, f: p["chat_id"] == ADMIN and "معاينة" in p["text"]); ok("البرودكاست: معاينة للأدمن قبل الإرسال")
        m = F.mark(); F.press(ADMIN, "adm:go")
        _, p, _ = F.wait("editMessageText", m, 60, lambda p, f: "خلص البرودكاست" in p["text"])
        sent = {int(c[1]["chat_id"]) for c in F.calls[m:] if c[0] == "copyMessage"}
        assert {ADMIN, USER, NOSUB, 6808448615} <= sent and 5464602559 in sent
        assert "عاملين بلوك: 2" in p["text"], p["text"]; ok("الإرسال: " + p["text"].replace("\u200f", "").replace("\n", " | "))

        print("== التعرّف على السلالة ==")
        from PIL import Image
        import numpy as np
        buf = io.BytesIO(); Image.fromarray(np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)).save(buf, "JPEG"); jpeg = buf.getvalue()
        model_dir = os.environ.get("CATTLE_MODEL_DIR")
        m = F.mark(); F.press(USER, "br"); F.wait("editMessageText", m, 20)
        if model_dir and os.path.exists(os.path.join(model_dir, "cattle_model.h5")):
            deadline = time.time() + 120                                          # الموديل بيتحمّل في الخلفية
            while time.time() < deadline:
                m = F.mark(); F.photo(USER, jpeg)
                try:
                    _, p, _ = F.wait("editMessageText", m, 25, lambda p, f: "نتيجة التعرّف" in p["text"] or "مش متاحة" in p["text"] or "مقدرتش" in p["text"])
                except TimeoutError: continue
                if "مش متاحة" in p["text"]: time.sleep(6); continue
                break
            assert "نتيجة التعرّف" in p["text"], p["text"]
            ok("صورة ← الموديل الحقيقي ← رد: " + " | ".join(p["text"].replace("\u200f", "").splitlines()[2:5]))
            m = F.mark(); F.press(USER, "fb:n"); F.wait("editMessageText", m, 20, lambda p, f: "السلالة الصح" in p["text"])
            m = F.mark(); F.press(USER, "fb:t3"); F.wait("editMessageText", m, 20, lambda p, f: "تحب تبعت" in p["text"])
            m = F.mark(); time.sleep(0.3); assert not [c for c in F.calls[m:] if c[0] == "sendPhoto"]            # من غير موافقة: مفيش صورة بتتبعت
            m = F.mark(); F.press(USER, "fb:s"); _, p, _ = F.wait("sendPhoto", m, 20, lambda p, f: p["chat_id"] == ADMIN)
            assert "truth=Hereford" in p["caption"] and p["photo"].startswith("F"); ok("تقييم ❌ + اختيار السلالة الصح + موافقة: الصورة وصلت لشات الأدمن بالتصنيف")
            m = F.mark(); F.photo(USER, jpeg); time.sleep(1.0)
            assert any("استنى" in c[1].get("text", "") for c in F.calls[m:] if c[0] == "sendMessage"); ok("صورتين ورا بعض: التانية بتستنى (cooldown)")
            time.sleep(4.5); m = F.mark(); F.photo(USER, jpeg, doc=True)
            F.wait("editMessageText", m, 30, lambda p, f: "نتيجة التعرّف" in p["text"]); ok("صورة مبعوتة كملف (document) اشتغلت برضه")
        else:
            m = F.mark(); F.photo(USER, jpeg); F.wait("sendMessage", m, 20, lambda p, f: "مش متاحة" in p["text"]); ok("من غير موديل: رسالة «مش متاحة» بدل ما يقع")

        panel = F.mark(); F.press(ADMIN, "adm:panel"); _, p, _ = F.wait("editMessageText", panel, 20, lambda p, f: "لوحة الأدمن" in p["text"])
        total1 = int(re.search(r"إجمالي المستخدمين: (\d+)", p["text"]).group(1)); print("  لوحة الأدمن:", p["text"].replace("\u200f", "").splitlines()[2:6])
        assert "fb 1" in p["text"] and "imported" in p["text"]
        if model_dir: assert "❌ 1" in p["text"] and "اتبعتت للتحسين: 1" in p["text"], p["text"]

        print("== إيقاف (SIGTERM) → نسخة احتياطية ==")
        m = F.mark(); rc = stop_bot(proc); assert rc == 0, rc
        _, p, files = F.wait("sendDocument", m, 5, lambda p, f: int(p["chat_id"]) == ADMIN and f["document"][0] == "bot_data.json")
        F.wait("pinChatMessage", m, 5); backup = json.loads(files["document"][1]); assert len(backup["users"]) == total1
        ok(f"الإيقاف الهادي حفظ نسخة احتياطية ({len(backup['users'])} مستخدم) وثبّتها في شات الأدمن، والعملية خرجت بكود 0")

        print("== تشغيل 2 (قرص جديد من غير ملف محلي) ==")
        proc, log = start_bot(port, os.path.join(tmp, "run2_fresh.json")); m = F.mark()
        F.wait("getFile", m, 60); ok("البوت جاب النسخة الاحتياطية من الرسالة المثبّتة عند التشغيل")
        time.sleep(1); m = F.mark(); F.press(ADMIN, "adm:panel"); _, p, _ = F.wait("editMessageText", m, 30, lambda p, f: "لوحة الأدمن" in p["text"])
        total2 = int(re.search(r"إجمالي المستخدمين: (\d+)", p["text"]).group(1)); assert total2 == total1, (total1, total2)
        m = F.mark(); F.press(USER, "m"); F.wait("editMessageText", m, 20); txt = F.msgs[F.last[USER]]["text"]
        assert "600.00 كجم" in txt; ok(f"بعد إعادة التشغيل: {total2} مستخدم رجعوا، وخلطة المستخدم (600 كجم ذرة) لسه موجودة")
        m = F.mark(); rc = stop_bot(proc); assert rc == 0; time.sleep(0.5)
        assert not [c for c in F.calls[m:] if c[0] in ("sendDocument", "editMessageMedia")]; ok("إيقاف من غير أي تغيير: ما رفعش نسخة جديدة (توفير)")
        proc, log = start_bot(port, os.path.join(tmp, "run3_fresh.json")); m = F.mark(); F.wait("getFile", m, 60)
        for d in ("s:5", "d:5:10"): mm = F.mark(); F.press(USER, d); F.wait("editMessageText", mm, 30)
        m = F.mark(); rc = stop_bot(proc); assert rc == 0
        _, p, files = F.wait("editMessageMedia", m, 5)
        assert not [c for c in F.calls[m:] if c[0] == "sendDocument"]
        assert json.loads(files[p["media"]["media"].replace("attach://", "")][1])["users"][str(USER)]["qty"]["5"] == 610
        ok("بعد تغيير (610 كجم): الإيقاف حدّث نفس الرسالة المثبّتة (editMessageMedia) بدل رسالة جديدة، والمحتوى متحدّث")
        print("\nALL E2E TESTS OK"); return 0
    except Exception:
        import traceback; traceback.print_exc()
        if log: log.flush(); print("---- bot log tail ----"); print(open(log.name, encoding="utf-8", errors="ignore").read()[-2500:])
        return 1
    finally:
        if proc and proc.poll() is None: proc.kill()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
