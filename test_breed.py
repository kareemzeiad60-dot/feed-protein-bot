"""
اختبار التعرّف على السلالة بالموديل الحقيقي. بيتخطّى نفسه لو الموديل مش موجود.
    CATTLE_MODEL_DIR=/path/to/models CATTLE_TEST_IMAGE=/path/to/cow.jpg python test_breed.py
"""
import asyncio, io, json, os, sys, tempfile
from unittest.mock import AsyncMock, MagicMock
import numpy as np
from PIL import Image

MODEL_DIR = os.environ.get("CATTLE_MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
IMG = os.environ.get("CATTLE_TEST_IMAGE")
if not os.path.exists(os.path.join(MODEL_DIR, "cattle_model.h5")):
    print("SKIP: model not found in", MODEL_DIR); sys.exit(0)
os.environ["CATTLE_MODEL_DIR"] = MODEL_DIR
os.environ["DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "d.json")
import breed, bot, storage

clf = breed.BreedClassifier(MODEL_DIR)
assert clf.load() and clf.available and len(clf.labels) == 9, clf.error
print("loaded:", clf.labels)

# صورة اختبار: الصورة اللي في ريبو الموديل لو موجودة، وإلا صورة اصطناعية
if IMG and os.path.exists(IMG):
    base = Image.open(IMG).convert("RGB"); base.thumbnail((1600, 1600))
else:
    rng = np.random.default_rng(0); base = Image.fromarray(rng.integers(0, 255, (600, 800, 3), dtype=np.uint8))
def jpeg(im, **kw):
    b = io.BytesIO(); im.save(b, "JPEG", quality=90, **kw); return b.getvalue()
data = jpeg(base)

# 1) مطابقة تامة مع تجهيز server.py الأصلي (حرفياً) على نفس الصورة
import tensorflow as tf
m = tf.keras.models.load_model(os.path.join(MODEL_DIR, "cattle_model.h5"), compile=False)
ref = m.predict(np.expand_dims(np.array(Image.open(io.BytesIO(data)).convert("RGB").resize((224, 224)), dtype=np.float32), 0), verbose=0)[0]
got = clf.predict(data)
exp = sorted(zip(clf.labels, ref), key=lambda t: -t[1])[:3]
assert [g[0] for g in got] == [e[0] for e in exp] and all(abs(g[1] - float(e[1])) < 1e-5 for g, e in zip(got, exp))
print("1) matches original server.py preprocessing:", [(l, round(p, 3)) for l, p in got])

# 2) صورة موبايل متدوّرة بـ EXIF لازم تتعامل زي الصورة المتدوّرة فعلياً
exif = Image.Exif(); exif[0x0112] = 6                       # 6 = لازم تتلف 90° مع عقارب الساعة
tagged = clf.predict(jpeg(base.rotate(90, expand=True).rotate(-90, expand=True), exif=exif.tobytes()))
plain_rot = clf.predict(jpeg(base.rotate(-90, expand=True)))
assert tagged[0][0] == plain_rot[0][0] and abs(tagged[0][1] - plain_rot[0][1]) < 0.05, (tagged, plain_rot)
print("2) EXIF-rotated photo handled like a physically rotated one")

# 3) حماية: موديل وتصنيفات مش متطابقين لازم يتقفل
bad = tempfile.mkdtemp(); os.symlink(os.path.join(MODEL_DIR, "cattle_model.h5"), os.path.join(bad, "cattle_model.h5"))
json.dump(clf.labels[:8], open(os.path.join(bad, "labels.json"), "w"))
c2 = breed.BreedClassifier(bad); assert not c2.load() and "outputs" in c2.error; print("3) mismatched labels rejected:", c2.error)
assert not breed.BreedClassifier("/nonexistent").load()
try: clf.predict(b"not an image"); raise SystemExit("should fail")
except Exception as e: print("4) bad bytes raise:", type(e).__name__)

# 5) النص
for p in (0.9, 0.55, 0.2):
    t = breed.format_result([("Holstein-Friesian", p), ("Jersey", 0.05), ("Angus", 0.02)], 9)
    assert "هولشتاين فريزيان" in t and "جيرسي" in t and "تقديري" in t and "ثقة الموديل" in t and ("💡" in t) == (p < 0.45) and "(" not in t and "←" not in t
print("5) result text OK:\n" + breed.format_result(got, 9))

# 6) من الهاندلر لحد الرد (تيليجرام متحاكى) بالموديل الحقيقي
async def flow():
    bot.CLASSIFIER = clf
    store = storage.Store(os.environ["DATA_FILE"], []); store.restored = True
    ctx = MagicMock(); ctx.application.bot_data = {"store": store, "admin_ids": [], "feeds": bot.FEEDS}; ctx.user_data = {}
    u = MagicMock(); u.effective_user.id = 7; u.effective_user.full_name = "x"; u.effective_user.username = "x"
    f = MagicMock(); f.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    photo = MagicMock(); photo.file_size = len(data); photo.get_file = AsyncMock(return_value=f)
    u.message.photo = [MagicMock(), photo]; u.message.document = None
    wait = MagicMock(); wait.edit_text = AsyncMock(); u.message.reply_text = AsyncMock(return_value=wait)
    await bot.on_photo(u, ctx)
    out = wait.edit_text.call_args.args[0]; assert "نتيجة التعرّف" in out and store.users[7]["breed_scans"] == 1
    rows = wait.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["fb:y", "fb:n"] and [b.callback_data for b in rows[1]] == ["br", "m"]
    print("6) handler -> reply with buttons: OK")
asyncio.run(flow())
print("ALL BREED TESTS OK")
