"""
بطاقة نتيجة العليقة (صورة PNG جاهزة للمشاركة): أرقام بارزة + أعمدة نسب المكونات.
الخط: IBM Plex Sans Arabic (SIL OFL) من assets/fonts.
التشكيل والاتجاه عبر arabic-reshaper + python-bidi، مع فرض المحرك البسيط في Pillow (Layout.BASIC)
عشان النتيجة تبقى واحدة على أي جهاز حتى لو مفيش libraqm.
"""
import io
import os

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "assets", "fonts")
W, PAD = 1080, 56
INK, MUTED, BG, CARD, TRACK = "#1B2A24", "#6B7C74", "#F5F2E9", "#FFFFFF", "#ECE8DB"
GREEN, GREEN_D, GOLD = "#2E7D5B", "#1F4D3A", "#E0A526"
PALETTE = ["#2E7D5B", "#E0A526", "#C8553D", "#4F86B5", "#8E6BBF", "#6BA368", "#D98E73", "#4F6D7A", "#B5A642", "#A0527C"]
MAX_ROWS = 10
_WEIGHTS = {"Regular": "Regular", "Medium": "Medium", "Bold": "Bold", "ExtraBold": "Bold"}

_reshaper = arabic_reshaper.ArabicReshaper(configuration={"support_ligatures": True})
_fonts = {}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _fonts:
        path = os.path.join(FONT_DIR, f"IBMPlexSansArabic-{_WEIGHTS[weight]}.ttf")
        _fonts[key] = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
    return _fonts[key]


def ar(text: str) -> str:
    """تشكيل الحروف العربية + ترتيب الاتجاه (للرسم بـ Pillow)."""
    return get_display(_reshaper.reshape(text))


def fit(d, text: str, f, max_w: int) -> str:
    """يقصّ النص بـ «…» لحد ما يبقى عرضه الفعلي جوه max_w."""
    if d.textlength(ar(text), font=f) <= max_w:
        return text
    while len(text) > 1 and d.textlength(ar(text + "…"), font=f) > max_w:
        text = text[:-1]
    return text + "…"


def _t(d, xy, text, f, fill, anchor):
    d.text(xy, ar(text), font=f, fill=fill, anchor=anchor)


def _num(x: float) -> str:
    return f"{x:,.2f}"


def render_card(res: dict, bot_username: str = "", target: float = None, advice_state: str = None) -> bytes:
    rows = sorted(res["lines"], key=lambda t: -t[1])
    if len(rows) > MAX_ROWS:                         # الخامات الصغيرة الأخيرة بتتجمّع في سطر «أخرى»
        head, tail = rows[:MAX_ROWS - 1], rows[MAX_ROWS - 1:]
        rows = head + [(None, sum(t[1] for t in tail), sum(t[2] for t in tail), sum(t[3] for t in tail), len(tail))]
    rows = [r if len(r) == 5 else r + (0,) for r in rows]

    tiles = [("البروتين الخام", f"{res['cp_pct']:.2f}", "%"), ("كمية البروتين", _num(res["protein_kg"]), "كجم"),
             ("الوزن الكلي", _num(res["total_weight"]), "كجم")]
    if res.get("cost_per_ton") is not None:
        tiles.append(("تكلفة الطن", f"{res['cost_per_ton']:,.0f}", "جنيه"))

    HEADER, TILE_H, ROW_H = 230, 190, 104
    pill_h = 108 if target else 0
    H = HEADER + 40 + TILE_H + 36 + (pill_h + 28 if target else 0) + 70 + len(rows) * ROW_H + 40 + 170
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ---- الهيدر
    d.rectangle((0, 0, W, HEADER), fill=GREEN_D)
    d.ellipse((-120, -160, 260, 220), fill="#265C46")
    d.ellipse((W - 240, HEADER - 120, W + 140, HEADER + 160), fill="#265C46")
    _t(d, (W - PAD, 56), "حاسبة العليقة", font("Bold", 76), "#FFFFFF", "ra")
    _t(d, (W - PAD, 154), "نتيجة الخلطة: البروتين ونسب المكونات", font("Medium", 34), "#BFE3D0", "ra")

    # ---- بطاقات الأرقام (البروتين على اليمين)
    y = HEADER + 40
    n, gap = len(tiles), 22
    tw = (W - 2 * PAD - gap * (n - 1)) // n
    for i, (label, value, unit) in enumerate(tiles):
        x1 = W - PAD - i * (tw + gap)
        x0 = x1 - tw
        hero = i == 0
        d.rounded_rectangle((x0, y, x1, y + TILE_H), radius=28, fill=GREEN if hero else CARD)
        fg, sub = ("#FFFFFF", "#CFEBDD") if hero else (INK, MUTED)
        cx = (x0 + x1) // 2
        _t(d, (cx, y + 20), label, font("Medium", 28), sub, "ma")
        d.text((cx, y + 62), value, font=font("Bold", 66 if n <= 3 or hero else 50), fill=fg, anchor="ma")
        _t(d, (cx, y + 140), unit, font("Medium", 28), sub, "ma")
    y += TILE_H + 36

    # ---- شريط الهدف
    if target:
        col = GREEN if advice_state == "ok" else (GOLD if advice_state in ("low", "high") else MUTED)
        d.rounded_rectangle((PAD, y, W - PAD, y + pill_h), radius=30, fill=CARD)
        d.rounded_rectangle((W - PAD - 16, y + 18, W - PAD - 4, y + pill_h - 18), radius=6, fill=col)
        state = {"ok": "على الهدف", "low": "أقل من الهدف", "high": "أعلى من الهدف"}.get(advice_state or "", "")
        _t(d, (W - PAD - 40, y + 12), f"الهدف {target:g}%  •  الحالي {res['cp_pct']:.2f}%", font("Bold", 38), INK, "ra")
        if state:
            _t(d, (W - PAD - 40, y + 64), state, font("Medium", 28), col, "ra")
        y += pill_h + 28

    # ---- عنوان القائمة
    _t(d, (W - PAD, y), "مكونات الخلطة", font("Bold", 42), INK, "ra")
    _t(d, (PAD, y + 12), "الكمية  •  النسبة", font("Medium", 26), MUTED, "la")
    y += 70

    # ---- الصفوف: [يمين] نقطة + اسم + بروتين | [نص] عمود النسبة | [شمال] النسبة والكمية
    maxshare = max(r[3] for r in rows) or 1
    bar_x0, bar_x1 = PAD + 190, PAD + 190 + 270
    name_right = W - PAD - 62
    name_w = name_right - (bar_x1 + 24)
    for i, (f, q, p, share, count) in enumerate(rows):
        color = PALETTE[i % len(PALETTE)] if f else "#9AA7A0"
        d.rounded_rectangle((PAD, y, W - PAD, y + ROW_H - 12), radius=22, fill=CARD)
        d.ellipse((W - PAD - 44, y + 26, W - PAD - 20, y + 50), fill=color)
        name = f.name_ar if f else f"خامات أخرى ({count})"
        _t(d, (name_right, y + 10), fit(d, name, font("Bold", 31), name_w), font("Bold", 31), INK, "ra")
        sub = f"بروتين {_num(p)} كجم" if p > 0 else "من غير بروتين"
        _t(d, (name_right, y + 54), sub, font("Regular", 24), MUTED, "ra")
        d.rounded_rectangle((bar_x0, y + 34, bar_x1, y + 58), radius=12, fill=TRACK)
        fw = max(12, int((bar_x1 - bar_x0) * share / maxshare))
        d.rounded_rectangle((bar_x1 - fw, y + 34, bar_x1, y + 58), radius=12, fill=color)
        d.text((PAD + 26, y + 10), f"{share * 100:.1f}%", font=font("Bold", 36), fill=INK, anchor="la")
        _t(d, (PAD + 26, y + 56), f"{q:,.1f} كجم", font("Regular", 24), MUTED, "la")
        y += ROW_H

    # ---- الفوتر
    y += 22
    d.line((PAD, y, W - PAD, y), fill="#DAD5C4", width=2)
    if bot_username:
        d.text((W // 2, y + 24), f"@{bot_username}", font=font("Bold", 44), fill=GREEN_D, anchor="ma")
    _t(d, (W // 2, y + (86 if bot_username else 30)), "الأرقام تقديرية، ومش بديل لأخصائي تغذية", font("Regular", 26), MUTED, "ma")
    end = y + (140 if bot_username else 84)

    out = io.BytesIO()
    img.crop((0, 0, W, end)).save(out, "PNG", optimize=True)
    return out.getvalue()
