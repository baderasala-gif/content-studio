# -*- coding: utf-8 -*-
"""
textrules.py - אכיפת כללי הכתיבה של באדר על טקסט שחזר מהמודל.

הכללים הקשיחים: בלי אימוג'ים, בלי קו מפריד ארוך. המודל מתבקש לכבד אותם,
והמנוע אוכף אותם גם אם המודל החליק. הדוח שמוחזר מראה מה תוקן.
"""
from __future__ import annotations

import re

# טווחי אימוג'ים וסמלים גרפיים. לא כולל חיצים וסימני פיסוק רגילים.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoticons, symbols, pictographs, flags, extended
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U00002B00-\U00002BFF"  # misc symbols and arrows (stars, squares)
    "\U00002300-\U000023FF"  # misc technical (watch, hourglass)
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "\U0000FE0F"             # variation selector 16
    "\U0000200D"             # zero width joiner
    "\U000020E3"             # combining enclosing keycap
    "\U00003030\U0000303D\U00003297\U00003299"
    "]+",
    flags=re.UNICODE,
)

# קו מפריד ארוך: em dash, horizontal bar, ו-en dash כשהוא משמש כמפריד (עם רווחים).
# רק רווחים וטאבים, לא שורות חדשות, כדי לא למזג שורות.
_LINE_START_DASH_RE = re.compile(r"(?m)^[ \t]*[–—―][ \t]+")
_EM_DASH_RE = re.compile(r"[ \t]*[—―]+[ \t]*")
_SPACED_EN_DASH_RE = re.compile(r"[ \t]+–[ \t]+")
_HR_LINE_RE = re.compile(r"^\s*[-_*]{3,}\s*$", flags=re.MULTILINE)


def strip_emojis(text: str) -> tuple[str, int]:
    found = _EMOJI_RE.findall(text)
    count = sum(len(m) for m in found)
    if not count:
        return text, 0
    cleaned = _EMOJI_RE.sub("", text)
    # רווחים כפולים שנשארו אחרי אימוג'י שהוסר
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    return cleaned, count


def fix_dashes(text: str) -> tuple[str, int]:
    # קו בתחילת שורה (תבליט) הופך למקף רגיל ולא לפסיק
    bullets = len(_LINE_START_DASH_RE.findall(text))
    cleaned = _LINE_START_DASH_RE.sub("- ", text)
    count = bullets + len(_EM_DASH_RE.findall(cleaned)) + len(_SPACED_EN_DASH_RE.findall(cleaned))
    if not count:
        return text, 0
    # קו בסוף שורה: נמחק בלי פסיק
    cleaned = re.sub(r"[ \t]*[—―]+[ \t]*(?=\n|$)", "", cleaned)
    cleaned = _EM_DASH_RE.sub(", ", cleaned)
    cleaned = _SPACED_EN_DASH_RE.sub(", ", cleaned)
    # ניקוי פסיקים כפולים, פסיק אחרי או לפני סימן פיסוק, פסיק בתחילת שורה
    cleaned = re.sub(r",[ \t]*,", ",", cleaned)
    cleaned = re.sub(r"([.!?:;])[ \t]*,[ \t]*", r"\1 ", cleaned)
    cleaned = re.sub(r",[ \t]*([.!?:;])", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^,[ \t]*", "", cleaned)
    return cleaned, count


def strip_hr_lines(text: str) -> tuple[str, int]:
    found = _HR_LINE_RE.findall(text)
    if not found:
        return text, 0
    cleaned = _HR_LINE_RE.sub("", text)
    return cleaned, len(found)


def tidy_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def enforce(text: str) -> tuple[str, dict]:
    """מחזיר (טקסט נקי, דוח). הדוח: כמה אימוג'ים הוסרו, כמה קווים הוחלפו."""
    text, emojis = strip_emojis(text or "")
    text, dashes = fix_dashes(text)
    text, hrs = strip_hr_lines(text)
    text = tidy_whitespace(text)
    report = {
        "emojis_removed": emojis,
        "dashes_replaced": dashes,
        "rules_removed": hrs,
        "changed": bool(emojis or dashes or hrs),
    }
    return text, report


if __name__ == "__main__":
    sample = "שלום 👋 זה מבחן — עם קו ארוך – ועוד ✅\n---\nסוף"
    clean, rep = enforce(sample)
    print(clean)
    print(rep)
