"""
منطق الحساب والتعامل مع ملف الإكسل (بدون أي اعتماد على تيليجرام).
Core logic: read feeds from the template, parse user text, fill the workbook, compute protein.
"""
import difflib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import openpyxl

FIRST_ROW, LAST_ROW = 5, 25          # صفوف المواد العلفية في الشيت الأول
QTY_COL = 2                          # العمود B = الكمية (الخلايا الوحيدة المفتوحة للتعديل)
ADDITIVE_FIRST_ROW = 20              # من الصف 20 لـ 25 إضافات (الخلايا الخضراء) وليها جرعات قياسية

# أسماء بديلة شائعة (المفتاح = الاسم الإنجليزي في الملف). تقدر تزود عليها براحتك.
ALIASES = {
    "Yellow Corn": ["ذرة", "درة", "ذره صفراء"],
    "Wheat Bran": ["ردة", "نخالة", "نخاله", "رده", "bran"],
    "Soybean Meal (44%)": ["صويا", "كسب صويا", "فول صويا", "soy", "soya"],
    "Cottonseed Meal (24%)": ["قطن", "كسب قطن", "cottonseed"],
    "Undecorticated Cottonseed Meal": ["قطن غير مقشور", "قطن بقشره"],
    "Linseed Meal": ["كتان", "كسب كتان"],
    "Peanut Meal": ["سوداني", "كسب سوداني", "فول سوداني"],
    "Sunflower Meal": ["عباد شمس", "كسب عباد"],
    "Sugar Beet Pulp": ["تفل", "بنجر"],
    "Molasses": ["مولاس", "عسل اسود"],
    "Faba Bean Screenings": ["حت فول", "فول"],
    "Sorghum": ["دره رفيعه", "ذره بيضا", "درة بيضاء"],
    "Ground Barley": ["شعير"],
    "Vegetable Oil / Fat": ["زيت", "دهن", "دهون"],
    "Sodium Bicarbonate": ["صودا", "بيكربونات", "بيكربونات صوديوم"],
    "Premix (Vitamins & Minerals)": ["بريمكس", "premix", "فيتامينات", "املاح معدنيه"],
    "Common Salt (NaCl)": ["ملح"],
    "Limestone (Calcium Carbonate)": ["حجر جيري", "جير", "كالسيوم", "limestone"],
    "Mycotoxin Binder": ["مضاد سموم", "سموم فطريه", "ممتز سموم"],
    "Live Yeast": ["خميره", "خمائر", "yeast"],
}

_STOP = {"كجم", "كيلو", "كغ", "كج", "جم", "kg", "kgs", "من", "ب"}


# ----------------------------------------------------------------------------
# تطبيع النص (عربي / إنجليزي)
# ----------------------------------------------------------------------------
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def norm(text: str) -> str:
    t = text.lower().translate(_AR_DIGITS)
    t = re.sub(r"[\u064B-\u065F\u0640]", "", t)          # تشكيل وتطويل
    t = t.translate(str.maketrans("أإآٱىة", "اااايه"))
    t = re.sub(r"\([^)]*\)", " ", t)                      # شيل (44%) وما شابه
    t = re.sub(r"[^\w\s]|\d|_", " ", t)
    return " ".join(w for w in t.split() if w not in _STOP)


# ----------------------------------------------------------------------------
# قراءة المواد من القالب
# ----------------------------------------------------------------------------
@dataclass
class Feed:
    row: int
    name_ar: str
    name_en: str
    dm: float          # المادة الجافة (كسر)
    cp_dm: float       # البروتين على أساس المادة الجافة (كسر)
    default_qty: float
    names: list        # أسماء مطبّعة للمطابقة

    @property
    def label(self) -> str:
        return self.name_ar

    @property
    def cp_as_fed(self) -> float:
        return self.dm * self.cp_dm


def load_feeds(template_path: str) -> list:
    """بيقرا الأسماء والنسب من الملف نفسه، فلو عدّلت القالب البوت هيتبعه."""
    wb = openpyxl.load_workbook(template_path)
    ws1, ws2 = wb.worksheets[0], wb.worksheets[1]
    feeds = []
    for r in range(FIRST_ROW, LAST_ROW + 1):
        ar, _, en = str(ws1.cell(r, 1).value).partition("\n")
        ar, en = ar.strip(), en.strip()
        default = float(ws1.cell(r, QTY_COL).value or 0) if r >= ADDITIVE_FIRST_ROW else 0.0
        variants = [ar, en] + ALIASES.get(en, [])
        feeds.append(Feed(
            row=r, name_ar=ar, name_en=en,
            dm=float(ws1.cell(r, 3).value),
            cp_dm=float(ws2.cell(r, 4).value),   # البروتين % في شيت المادة الجافة
            default_qty=default,
            names=[n for n in dict.fromkeys(norm(v) for v in variants) if n],
        ))
    return feeds


def default_quantities(feeds) -> dict:
    return {f.row: f.default_qty for f in feeds}


# ----------------------------------------------------------------------------
# تحليل رسالة المستخدم
# ----------------------------------------------------------------------------
def _score(q: str, feed: Feed) -> float:
    qt = set(q.split())
    best = 0.0
    for n in feed.names:
        nt = n.split()
        if q == n:
            s = 1.0
        elif qt and qt <= set(nt):
            s = 0.9 - 0.01 * (len(nt) - len(qt))
        elif q in n:
            s = 0.8
        else:
            r = max([difflib.SequenceMatcher(None, q, n).ratio()] +
                    [difflib.SequenceMatcher(None, q, t).ratio() for t in nt])
            s = r * 0.9
        best = max(best, s)
    return best


def match_feed(query: str, feeds):
    """يرجّع (feed, None) لو لقى مادة واحدة واضحة، أو (None, [candidates]) لو مش واضح."""
    q = norm(query)
    if not q:
        return None, []
    ranked = sorted(((_score(q, f), f) for f in feeds), key=lambda x: -x[0])
    top, best = ranked[0]
    if top < 0.6:
        return None, []
    close = [f for s, f in ranked if s >= top - 0.03]
    if len(close) > 1 and top < 1.0:
        return None, close
    return best, None


def _clean_number_text(line: str) -> str:
    s = line.translate(_AR_DIGITS).replace("٫", ".").replace("٬", "")
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)   # 1,000 -> 1000
    return s.replace(",", ".").replace("،", " ")


def parse_message(text: str, feeds):
    """
    كل سطر: اسم المادة + الكمية (بالكيلو). أمثلة:
        ذرة صفراء 500   |   soybean meal: 250   |   ردة 100 كجم
    يرجّع (updates{row: qty}, errors[str]).
    """
    updates, errors = {}, []
    for raw in re.split(r"[\n;؛]+", text):
        line = raw.strip()
        if not line:
            continue
        s = _clean_number_text(line)
        nums = list(re.finditer(r"\d+(?:\.\d+)?", s))
        if not nums:
            errors.append(f"«{line}» — مفيش كمية فيه")
            continue
        m = nums[-1]
        qty = float(m.group())
        name = s[:m.start()] + " " + s[m.end():]
        if qty > 1_000_000:
            errors.append(f"«{line}» — الكمية كبيرة أوي")
            continue
        feed, cands = match_feed(name, feeds)
        if feed:
            updates[feed.row] = qty
        elif cands:
            errors.append(f"«{line}» — مش متأكد تقصد أنهي: " + " / ".join(c.name_ar for c in cands)
                          + "\nابعتها تاني بالاسم كامل")
        else:
            errors.append(f"«{line}» — ملقيتش مادة بالاسم ده (جرّب /feeds)")
    return updates, errors


# ----------------------------------------------------------------------------
# الحساب (نفس معادلات الملف بالظبط)
# ----------------------------------------------------------------------------
def compute(quantities: dict, feeds) -> dict:
    """E = الكمية × (DM% × CP_dm%)  ،  CP% = إجمالي البروتين ÷ إجمالي الوزن."""
    lines, total_w, total_p = [], 0.0, 0.0
    for f in feeds:
        q = quantities.get(f.row, 0.0)
        p = q * f.cp_as_fed
        total_w += q
        total_p += p
        if q > 0:
            lines.append((f, q, p))
    return {
        "lines": lines,
        "total_weight": total_w,
        "protein_kg": total_p,
        "cp_pct": (total_p / total_w * 100) if total_w > 0 else 0.0,
    }


# ----------------------------------------------------------------------------
# تعديل الملف
# ----------------------------------------------------------------------------
def build_workbook(template_path: str, quantities: dict, out_path: str, recalc: bool = False) -> str:
    """
    بينسخ القالب ويكتب الكميات في العمود B بس (الخلايا المفتوحة)،
    وكل المعادلات والحماية والتنسيق زي ما هم. الإكسل بيحسب لما الملف يتفتح.
    """
    wb = openpyxl.load_workbook(template_path)
    ws = wb.worksheets[0]
    for row, qty in quantities.items():
        ws.cell(row, QTY_COL).value = qty
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)
    if recalc:
        _recalc_with_libreoffice(out_path)
    return out_path


def _recalc_with_libreoffice(path: str) -> None:
    """اختياري: بيخلّي القيم محفوظة جوه الملف عشان تظهر في معاينات الموبايل. لو فشل بنسيب الملف زي ما هو."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [soffice, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
                 "--outdir", tmp, path],
                check=True, capture_output=True, timeout=90,
            )
            out = os.path.join(tmp, os.path.basename(path))
            if os.path.exists(out):
                shutil.copyfile(out, path)
        except Exception:
            pass
