"""فاحص بسيط لصيغة HTML اللي بيقبلها تيليجرام (للاختبارات): وسوم مسموحة ومتوازنة، ومفيش < > & سايبة من غير هروب."""
import re
from html.parser import HTMLParser

ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "blockquote", "tg-spoiler", "tg-emoji", "span"}
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|lt|gt|amp|quot);")


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"unsupported tag <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unmatched </{tag}> (stack={self.stack})")
        else:
            self.stack.pop()


def validate_html(text: str):
    """يرجّع قايمة أخطاء (فاضية = سليم)."""
    errs = []
    p = _P()
    p.feed(text); p.close()
    errs += p.errors
    if p.stack:
        errs.append(f"unclosed tags: {p.stack}")
    stripped = re.sub(r"<[^<>]*>", "", text)               # شيل الوسوم
    stripped = _ENTITY.sub("", stripped)                    # وشيل الـ entities الصحيحة
    for ch in "<>&":
        if ch in stripped:
            errs.append(f"unescaped '{ch}' in text")
    return errs
