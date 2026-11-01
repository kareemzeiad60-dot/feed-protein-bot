"""
منطق الحساب والتعامل مع ملف الإكسل (بدون أي اعتماد على تيليجرام).
Core logic: read feeds from the template, parse user text, fill the workbook, compute protein.
"""
import copy
import difflib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import openpyxl

FIRST_ROW, LAST_ROW = 5, 25          # صفوف المواد العلفية في الشيت الأول
QTY_COL = 2                          # العمود B = الكمية (خلايا مفتوحة للتعديل)
PRICE_COL = 11                       # العمود K = سعر الطن بالجنيه (مفتوح كمان)
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


def parse_number(text: str):
    """رقم لوحده (مع كجم/كيلو اختياري). يرجّع float أو None."""
    s = _clean_number_text(text)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:كجم|كيلو|كغ|kg)?\s*", s, re.I)
    return float(m.group(1)) if m else None


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
def compute(quantities: dict, feeds, prices: dict = None) -> dict:
    """
    E = الكمية × (DM% × CP_dm%)  ،  CP% = إجمالي البروتين ÷ إجمالي الوزن
    نسبة المكون = كميته ÷ إجمالي الخلطة.  التكلفة (جنيه/طن خلطة) = مجموع (سعر طن المكون × نسبته).
    """
    prices = prices or {}
    total_w = sum(quantities.get(f.row, 0.0) for f in feeds)
    lines, total_p, cost, missing = [], 0.0, 0.0, []
    for f in feeds:
        q = quantities.get(f.row, 0.0)
        if q <= 0:
            continue
        p = q * f.cp_as_fed
        share = q / total_w if total_w > 0 else 0.0
        total_p += p
        lines.append((f, q, p, share))
        price = prices.get(f.row, 0.0)
        if price > 0:
            cost += price * share
        else:
            missing.append(f)
    return {
        "lines": lines,
        "total_weight": total_w,
        "protein_kg": total_p,
        "cp_pct": (total_p / total_w * 100) if total_w > 0 else 0.0,
        "cost_per_ton": cost if any(prices.get(f.row, 0) > 0 for f, *_ in lines) else None,
        "missing_price": missing,
    }


def advice(res: dict, target_pct: float, feeds):
    """
    نصيحة للوصول للبروتين المستهدف بإضافة خامة واحدة:
      ناقص ← كسب صويا (أعلى بروتين) ، زايد ← ذرة صفراء (تخفيف).
    الحل: (P + x·cp) / (W + x) = t   ⇒   x = (t·W − P) / (cp − t)
    يرجّع (حالة, نص) حيث الحالة ok / low / high / none.
    """
    W, P, t = res["total_weight"], res["protein_kg"], target_pct / 100
    if W <= 0:
        return "none", ""
    diff = res["cp_pct"] - target_pct
    head = f"🎯 الهدف: {target_pct:g}%"
    if abs(diff) < 0.25:
        return "ok", f"{head}\n✅ الحالي: {res['cp_pct']:.2f}% وعلى الهدف"
    by_en = {f.name_en: f for f in feeds}
    if diff < 0:
        line = f"{head}\n⬇️ الحالي: {res['cp_pct']:.2f}% ناقص {abs(diff):.2f}%"
        src = by_en.get("Soybean Meal (44%)") or max(feeds, key=lambda f: f.cp_as_fed)
        if src.cp_as_fed > t:
            x = (t * W - P) / (src.cp_as_fed - t)
            line += f"\n💡 ضيف حوالي {x:,.1f} كجم {src.name_ar} عشان توصل للهدف"
        return "low", line
    line = f"{head}\n⬆️ الحالي: {res['cp_pct']:.2f}% زايد {diff:.2f}%"
    src = by_en.get("Yellow Corn") or min((f for f in feeds if f.cp_as_fed > 0), key=lambda f: f.cp_as_fed)
    if src.cp_as_fed < t:
        x = (P - t * W) / (t - src.cp_as_fed)
        line += f"\n💡 ضيف حوالي {x:,.1f} كجم {src.name_ar} عشان تنزل للهدف"
    return "high", line


def scale_quantities(quantities: dict, new_total: float) -> dict:
    """تحجيم الخلطة (كل الكميات بنفس النسبة) لحد ما الإجمالي يبقى new_total كجم."""
    total = sum(quantities.values())
    if total <= 0:
        return dict(quantities)
    k = new_total / total
    return {row: round(q * k, 2) for row, q in quantities.items()}


# ----------------------------------------------------------------------------
# تعديل الملف
# ----------------------------------------------------------------------------
_REF = re.compile(r"(\$?[A-Z]{1,3})(\$?)(\d+)(?::(\$?[A-Z]{1,3})(\$?)(\d+))?")
TOTAL_ROW = LAST_ROW + 1             # صف الإجمالي في القالب (26)


def _map_formula(f: str, new_of: dict, last_new: int, tot_new: int) -> str:
    """يعدّل أرقام الصفوف في الصيغة بعد حذف الخامات الفاضية (صف الإجمالي والنطاقات بتتظبط)."""
    def one(r):
        return tot_new if r == TOTAL_ROW else new_of.get(r, r)

    def sub(m):
        c1, d1, r1, c2, d2, r2 = m.groups()
        r1 = int(r1)
        if c2 is None:
            return f"{c1}{d1}{one(r1)}"
        r2 = int(r2)
        return f"{c1}{d1}{FIRST_ROW if r1 == FIRST_ROW else one(r1)}:{c2}{d2}{last_new if r2 == LAST_ROW else one(r2)}"
    return _REF.sub(sub, f)


def _compact_sheet(ws, table_cols: int, selected: list, keep_own_style: set):
    """يسيب في الجدول الخامات المختارة بس (بنفس الترتيب) وينقل صف الإجمالي تحتهم."""
    from openpyxl.styles.cell_style import StyleArray
    new_of = {old: FIRST_ROW + i for i, old in enumerate(selected)}
    last_new = FIRST_ROW + len(selected) - 1
    tot_new = last_new + 1
    snap = {(r, c): (ws.cell(r, c).value, copy.copy(ws.cell(r, c)._style))
            for r in range(FIRST_ROW, TOTAL_ROW + 1) for c in range(1, table_cols + 1)}
    for r in range(FIRST_ROW, TOTAL_ROW + 1):                       # امسح الجدول القديم (من غير عمود الملخص)
        for c in range(1, table_cols + 1):
            cell = ws.cell(r, c)
            cell.value, cell._style = None, StyleArray()

    def put(row, col, old_row, style_row):
        v, _ = snap[(old_row, col)]
        _, st = snap[(style_row, col)]
        cell = ws.cell(row, col)
        cell._style = copy.copy(st)
        cell.value = _map_formula(v, new_of, last_new, tot_new) if isinstance(v, str) and v.startswith("=") else v

    for i, old in enumerate(selected):
        zebra = FIRST_ROW if i % 2 == 0 else FIRST_ROW + 1          # تظليل متبادل بعد الضغط
        for c in range(1, table_cols + 1):
            put(FIRST_ROW + i, c, old, old if c in keep_own_style else zebra)
    for c in range(1, table_cols + 1):
        put(tot_new, c, TOTAL_ROW, TOTAL_ROW)
    for row in ws.iter_rows(min_col=table_cols + 1):                # صيغ الملخص (B26 -> صف الإجمالي الجديد...)
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.value = _map_formula(cell.value, new_of, last_new, tot_new)
    return new_of


def build_workbook(template_path: str, quantities: dict, out_path: str, recalc: bool = False, prices: dict = None, footer: tuple = None) -> str:
    """
    بينسخ القالب ويسيب فيه الخامات اللي كميتها > 0 بس (الباقي بيتشال من الشيتين)،
    ويكتب الكميات في العمود B والأسعار في العمود K. كل المعادلات (نسب المكونات، كجم/طن،
    التكلفة، الملخص) بتتعدّل على الصفوف الجديدة، والحماية والتنسيق زي ما هم.
    """
    wb = openpyxl.load_workbook(template_path)
    selected = [r for r in range(FIRST_ROW, LAST_ROW + 1) if quantities.get(r, 0) > 0]
    if not selected:
        raise ValueError("no ingredients with quantity > 0")
    ws1, ws2 = wb.worksheets[0], wb.worksheets[1]
    new_of = _compact_sheet(ws1, 12, selected, keep_own_style={QTY_COL, PRICE_COL})   # A..L : B و K خلايا إدخال ملونة
    _compact_sheet(ws2, 10, selected, keep_own_style=set())                          # A..J
    for old, new in new_of.items():
        ws1.cell(new, QTY_COL).value = quantities[old]
        price = (prices or {}).get(old, 0)
        if price and price > 0:
            ws1.cell(new, PRICE_COL).value = price
    if footer:                                                      # (نص, لينك) تحت الجدول — الملف بيتشاير في الجروبات
        from openpyxl.styles import Font
        cell = ws1.cell(FIRST_ROW + len(selected) + 2, 1)
        cell.value, cell.hyperlink = footer
        cell.font = Font(italic=True, color="0563C1", underline="single", size=10)
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
