"""
تخزين بيانات البوت (المستخدمين، الخلطات، الأسعار، الإحصائيات).

- ملف JSON محلي (bot_data.json) — بيشتغل على أي سيرفر/جهاز.
- نسخة احتياطية على تيليجرام: ملف JSON بيتثبّت في شات الأدمن مع البوت ويتحدّث لوحده.
  ده اللي بيخلّي البيانات تعدّي إعادة التشغيل على GitHub Actions (اللي بتمسح القرص كل مرة).
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from telegram.error import TelegramError

log = logging.getLogger("feedbot.store")
BACKUP_NAME = "bot_data.json"
BACKUP_MIN_INTERVAL = 300      # أقل فاصل (ثواني) بين نسختين احتياطيتين على تيليجرام
MAX_RECIPES = 20


def now() -> int:
    return int(time.time())


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _int_keys(d):
    return {int(k): float(v) for k, v in (d or {}).items()}


class Store:
    def __init__(self, path: str, admin_ids: list):
        self.path = path
        self.admin_ids = admin_ids
        self.users = {}        # uid -> dict (profile + qty + prices + recipes + target)
        self.daily = {}        # "YYYY-MM-DD" -> {"calcs": n}
        self.dirty = False
        self.restored = not admin_ids          # من غير أدمن مفيش نسخة تيليجرام نستعيدها
        self.backup_msg = None
        self._last_backup = 0.0
        self._lock = asyncio.Lock()

    # ---------------- تحميل / حفظ محلي ----------------
    def load_local(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path, encoding="utf-8") as fh:
                self._load_obj(json.load(fh))
            return True
        except Exception:
            log.exception("failed to read %s", self.path)
            return False

    def _load_obj(self, obj: dict):
        self.daily = obj.get("daily", {})
        self.users = {}
        for uid, u in obj.get("users", {}).items():
            u["qty"] = _int_keys(u.get("qty"))
            u["prices"] = _int_keys(u.get("prices"))
            u["recipes"] = {n: _int_keys(r) for n, r in (u.get("recipes") or {}).items()}
            self.users[int(uid)] = u

    def to_bytes(self) -> bytes:
        obj = {"daily": self.daily, "users": {str(k): v for k, v in self.users.items()}}
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def save_local(self):
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(self.to_bytes())
        os.replace(tmp, self.path)

    # ---------------- نسخة تيليجرام ----------------
    async def restore_from_telegram(self, bot) -> bool:
        """يرجّع True لو اتأكدنا (لقينا نسخة أو مفيش نسخة أصلاً)، False لو حصل خطأ اتصال."""
        if not self.admin_ids:
            return True
        try:
            chat = await bot.get_chat(self.admin_ids[0])
            pm = chat.pinned_message
            if pm and pm.document and pm.document.file_name == BACKUP_NAME:
                f = await pm.document.get_file()
                raw = bytes(await f.download_as_bytearray())
                self._merge(json.loads(raw.decode("utf-8")))
                self.backup_msg = pm.message_id
                log.info("restored %d users from Telegram backup", len(self.users))
            else:
                log.info("no Telegram backup found (first run)")
            self.restored = True
            return True
        except Exception as e:
            log.warning("restore from Telegram failed: %s", e)
            return False

    def _merge(self, obj: dict):
        """لو عندنا بيانات في الذاكرة بالفعل، بنضيف عليها القديم من غير ما نمسح الجديد."""
        mine_users, mine_daily = self.users, self.daily
        self._load_obj(obj)
        for uid, u in mine_users.items():
            self.users[uid] = u
        for d, v in mine_daily.items():
            self.daily[d] = v

    async def _backup_to_telegram(self, bot):
        payload = self.to_bytes()
        admin = self.admin_ids[0]
        caption = f"💾 نسخة احتياطية — ما تمسحش الرسالة دي ولا تثبّت رسائل تانية في الشات ({len(self.users)} مستخدم)"
        if self.backup_msg:
            try:
                from telegram import InputMediaDocument
                await bot.edit_message_media(
                    chat_id=admin, message_id=self.backup_msg,
                    media=InputMediaDocument(media=payload, filename=BACKUP_NAME, caption=caption))
                return
            except TelegramError as e:
                if "not modified" in str(e).lower():
                    return
                log.warning("edit backup failed (%s) — sending a new one", e)
                self.backup_msg = None
        msg = await bot.send_document(admin, document=payload, filename=BACKUP_NAME,
                                      caption=caption, disable_notification=True)
        self.backup_msg = msg.message_id
        try:
            await bot.pin_chat_message(admin, msg.message_id, disable_notification=True)
        except TelegramError as e:
            log.warning("pin failed: %s", e)

    async def flush(self, bot=None, force: bool = False):
        """حفظ محلي لو في تغييرات، ونسخة تيليجرام بحد أدنى فاصل زمني (force = عند الإيقاف)."""
        async with self._lock:
            if self.dirty:
                await asyncio.to_thread(self.save_local)
            if not (bot and self.admin_ids):
                self.dirty = False
                return
            if not self.restored:
                await self.restore_from_telegram(bot)
                if not self.restored:
                    return                       # مانرفعش نسخة جديدة فوق القديمة لو مش قادرين نقراها
            if self.dirty and (force or time.time() - self._last_backup >= BACKUP_MIN_INTERVAL):
                try:
                    await self._backup_to_telegram(bot)
                    self._last_backup = time.time()
                    self.dirty = False
                except Exception as e:
                    log.warning("telegram backup failed: %s", e)
                    if not force:
                        return
            elif not self.dirty:
                return

    # ---------------- بيانات المستخدمين ----------------
    def user(self, uid: int, defaults: dict = None) -> dict:
        u = self.users.get(uid)
        if u is None:
            u = {"name": "", "username": "", "joined": now(), "last_seen": now(), "calcs": 0,
                 "blocked": False, "banned": False, "target": None,
                 "qty": dict(defaults or {}), "prices": {}, "recipes": {}}
            self.users[uid] = u
            self.dirty = True
        return u

    def touch(self, uid: int, name: str, username: str, defaults: dict) -> dict:
        u = self.user(uid, defaults)
        u["name"], u["username"] = name or "", username or ""
        if now() - u.get("last_seen", 0) > 60 or u.get("blocked"):
            u["last_seen"] = now()
            u["blocked"] = False
            self.dirty = True
        return u

    def mark(self):
        self.dirty = True

    def count_calc(self, uid: int):
        self.users[uid]["calcs"] = self.users[uid].get("calcs", 0) + 1
        d = self.daily.setdefault(today(), {"calcs": 0})
        d["calcs"] += 1
        for k in sorted(self.daily)[:-60]:          # نحتفظ بآخر 60 يوم بس
            del self.daily[k]
        self.dirty = True

    def stats(self) -> dict:
        t = now()
        us = list(self.users.values())
        return {
            "total": len(us),
            "new_24h": sum(1 for u in us if t - u["joined"] < 86400),
            "active_24h": sum(1 for u in us if t - u["last_seen"] < 86400),
            "active_7d": sum(1 for u in us if t - u["last_seen"] < 7 * 86400),
            "blocked": sum(1 for u in us if u.get("blocked")),
            "banned": sum(1 for u in us if u.get("banned")),
            "calcs_total": sum(u.get("calcs", 0) for u in us),
            "calcs_today": self.daily.get(today(), {}).get("calcs", 0),
        }

    def broadcast_targets(self) -> list:
        return [uid for uid, u in self.users.items() if not u.get("blocked") and not u.get("banned")]
