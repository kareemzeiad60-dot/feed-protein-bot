"""
التعرّف على سلالة الماشية من صورة (موديل Keras/EfficientNet من مشروع Cattle Insight Hub).

- الموديل وملف التصنيفات بيتحمّلوا من مجلد models/ (أو CATTLE_MODEL_DIR). مش جزء من المستودع العام.
- لو الملفات أو TensorFlow مش موجودين، الميزة بتتقفل لوحدها والبوت يكمّل عادي.
- التجهيز نفس server.py الأصلي: RGB ← 224×224 ← قيم خام 0-255 (الموديل فيه preprocess جواه).
"""
import io
import json
import logging
import os
import threading

# numpy و Pillow و TensorFlow بيتستوردوا جوه الدوال (مش هنا): البوت لازم يشتغل حتى لو مش متسطّبين،
# والميزة دي بس هي اللي تتقفل.
log = logging.getLogger("feedbot.breed")
MAX_IMAGE_PIXELS = 60_000_000            # حماية من الصور الضخمة جداً
IMG_SIZE = (224, 224)
TOP_K = 3
MEDIUM = 0.45     # تحت الرقم ده: "مش متأكد". مفيش مستوى "ثقة عالية" عمداً: نسبة الثقة مش مؤشر موثوق على صور الحظائر

# الاسم بالإنجليزي زي ما هو في labels.json ← (الاسم بالعربي، النوع، وصف مختصر)
BREEDS = {
    "Angus": ("أنجس", "لحم", "سلالة لحم شهيرة، لونها أسود غالباً وبدون قرون"),
    "Ayrshire": ("إيرشاير", "لبن", "سلالة لبن، لونها أحمر مع أبيض"),
    "Brown Swiss": ("براون سويس", "لبن", "سلالة لبن كبيرة الحجم، لونها بني إلى رمادي"),
    "Hereford": ("هيرفورد", "لحم", "سلالة لحم، جسمها أحمر ووجهها أبيض"),
    "Holstein-Friesian": ("هولشتاين فريزيان", "لبن", "أشهر سلالة لبن، أبيض وأسود وإنتاجها من اللبن عالي"),
    "Jaffarabadi": ("جعفرآبادي", "جاموس", "سلالة جاموس هندية ضخمة الحجم"),
    "Jersey": ("جيرسي", "لبن", "سلالة لبن صغيرة الحجم، لونها بني فاتح ولبنها غني بالدهن"),
    "Red Dane": ("ريد دين الدنماركي الأحمر", "لبن ولحم", "سلالة دنماركية حمراء تُربّى للبن واللحم"),
    "Simmental": ("سيمنتال", "لبن ولحم", "سلالة سويسرية مزدوجة الغرض، حمراء مع أبيض"),
}


def arabic_name(label: str) -> str:
    return BREEDS.get(label, (label.replace("_", " "),))[0]


class BreedClassifier:
    def __init__(self, model_dir: str, model_file: str = "cattle_model.h5"):
        self.model_dir, self.model_file = model_dir, model_file
        self.model = None
        self.labels = []
        self.error = None
        self._lock = threading.Lock()      # Keras predict مش مضمون يكون thread-safe

    @property
    def available(self) -> bool:
        return self.model is not None

    def load(self) -> bool:
        path = os.path.join(self.model_dir, self.model_file)
        labels_path = os.path.join(self.model_dir, "labels.json")
        if not (os.path.exists(path) and os.path.exists(labels_path)):
            self.error = f"model files not found in {self.model_dir}"
            log.info("breed model disabled: %s", self.error)
            return False
        try:
            os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
            import numpy as np
            from PIL import Image                       # لازم يكون متسطّب (مش جاي مع TensorFlow)
            import tensorflow as tf                     # استيراد متأخر عشان البوت يبدأ بسرعة من غيره
            model = tf.keras.models.load_model(path, compile=False)
            with open(labels_path, encoding="utf-8") as fh:
                labels = json.load(fh)
            outputs = int(model.output_shape[-1])
            if outputs != len(labels):                   # حماية من ملفات مش متطابقة (حصلت في نسخ قديمة)
                raise ValueError(f"model has {outputs} outputs but labels.json has {len(labels)} entries")
            self.model, self.labels = model, labels
            buf = io.BytesIO()                                              # اختبار ذاتي للخط كله: فتح صورة ← تجهيز ← موديل
            Image.new("RGB", (64, 48), (120, 120, 120)).save(buf, "JPEG")
            self.predict(buf.getvalue())                                    # وبيسخّن الموديل كمان
            self.error = None
            log.info("breed model loaded and self-tested: %d breeds %s", len(labels), labels)
            return True
        except Exception as e:
            self.model, self.labels = None, []              # أي فشل = الميزة تتقفل بصراحة بدل ما تقع عند كل صورة
            self.error = f"{type(e).__name__}: {e}"
            log.warning("breed model disabled: %s", self.error)
            return False

    def predict(self, image_bytes: bytes):
        """يرجّع أعلى 3 سلالات [(label, probability)]."""
        import numpy as np
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB").resize(IMG_SIZE)
        x = np.expand_dims(np.array(img, dtype=np.float32), 0)
        with self._lock:
            probs = self.model.predict(x, verbose=0)[0]
        top = np.argsort(probs)[::-1][:TOP_K]
        return [(self.labels[int(i)], float(probs[i])) for i in top]


def format_result(preds, n_breeds: int) -> str:
    """نص النتيجة (سطور قصيرة، من غير أقواس/أسهم عشان العرض العربي ما يتلخبطش)."""
    label, p = preds[0]
    ar, kind, note = BREEDS.get(label, (label.replace("_", " "), "", ""))
    verdict = "🟡 توقع تقريبي، اتأكد بنفسك" if p >= MEDIUM else "🔴 ثقة الموديل ضعيفة، مش متأكد"
    lines = ["🐄 نتيجة التعرّف على السلالة", "", f"🥇 {ar}", f"   نسبة ثقة الموديل: {p * 100:.1f}%", f"   {verdict}"]
    if kind:
        lines.append(f"   النوع: {kind}")
    if note:
        lines.append(f"   {note}")
    for medal, (lab, pr) in zip(("🥈", "🥉"), preds[1:3]):
        lines.append(f"{medal} {arabic_name(lab)}: {pr * 100:.1f}%")
    if p < MEDIUM:
        lines += ["", "💡 جرّب صورة جانبية واضحة للحيوان كله وفي إضاءة كويسة."]
    lines += ["", f"⚠️ التعرّف من الصورة تقديري، والموديل متدرّب على {n_breeds} سلالات بس. "
                  "لو الصورة مش بقرة أو جاموس فالنتيجة مالهاش قيمة.",
              "ودقته بتقل جداً مع الصور جوه الحظائر أو في إضاءة ضعيفة أو ضد الشمس. الأفضل صورة جانبية للحيوان كله بالنهار."]
    return "\n".join(lines)
