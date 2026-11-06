"""اختبار موديل DOMESTIC (models/domestic) — بيشتغل بدون أي ملفات خارج الريبو."""
import io, json, os
import numpy as np
from PIL import Image
import breed

HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "models", "domestic")
clf = breed.BreedClassifier(D, "cattle_model.h5", preprocess="unit", title="domestic")
assert clf.load() and len(clf.labels) == 10, clf.error
print("loaded + self-tested:", clf.labels)

# كل التصنيفات لها اسم عربي ونوع (مفيش حاجة بتظهر بالإنجليزي الخام)
for lab in clf.labels:
    ar = breed.arabic_name(lab); kind = breed.BREEDS[breed.norm_label(lab)][1]
    assert ar and ar != lab.replace("_", " ") and kind, lab
assert breed.norm_label("Holstein_Friesian") == breed.norm_label("Holstein-Friesian")
print("10/10 labels have Arabic names:", [breed.arabic_name(l) for l in clf.labels])

# التجهيز لازم يطابق مرجع core.py الأصلي (resize 224 ثم /255) بالظبط
import tensorflow as tf
m = tf.keras.models.load_model(os.path.join(D, "cattle_model.h5"), compile=False)
rng = np.random.default_rng(3); img = Image.fromarray(rng.integers(0, 255, (480, 640, 3), dtype=np.uint8))
b = io.BytesIO(); img.save(b, "JPEG", quality=90); data = b.getvalue()
ref = m.predict(np.expand_dims(np.array(Image.open(io.BytesIO(data)).convert("RGB").resize((224, 224)), dtype=np.float32) / 255.0, 0), verbose=0)[0]
got = clf.predict(data); exp = sorted(zip(clf.labels, ref), key=lambda t: -t[1])[:3]
assert [g[0] for g in got] == [e[0] for e in exp] and all(abs(g[1] - float(e[1])) < 1e-5 for g, e in zip(got, exp))
print("matches the source repo's core.py preprocessing (x/255):", [(l, round(p, 3)) for l, p in got])
raw = breed.BreedClassifier(D, "cattle_model.h5", preprocess="raw"); assert raw.load()
assert abs(raw.predict(data)[0][1] - got[0][1]) > 1e-3 or raw.predict(data)[0][0] != got[0][0]       # 0-255 بيدّي نتيجة مختلفة، يعني الوضع بيأثر فعلاً
t = breed.format_result([("BRAFORD", 0.8), ("Hereford", 0.1), ("BRANGUES NEGRO", 0.05)], 10)
assert "برافورد" in t and "هجين" in t and "برانجوس الأسود" in t and "BRAFORD" not in t
print("ALL DOMESTIC TESTS OK")
