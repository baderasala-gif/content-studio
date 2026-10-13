# -*- coding: utf-8 -*-
"""
pricing.py - הערכת עלות לכל קריאה (Anthropic ו-fal.ai).

המחירים מתוארכים. עלות של קריאה מחושבת בזמן הרישום ונשמרת ברשומת השימוש של הריצה,
כך ששינוי מחירים עתידי לא משכתב היסטוריה. basis: "list" = לפי מחירון מפורסם,
"estimate" = הערכה (אינטרפולציה או מחיר לא מאומת). כל הסכומים בדולר ארה"ב.
"""
from __future__ import annotations

PRICING_DATE = "2026-10-08"
SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing (נקרא 08.10.2026)",
    "fal": "https://fal.ai/gpt-image-2.5 ו-https://fal.ai/models/fal-ai/nano-banana-2 (נקראו 08.10.2026)",
}

# דולר למיליון טוקנים: (קלט, פלט). מחירי בסיס, בלי מטמון.
ANTHROPIC_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
WEB_SEARCH_PER_1000 = 10.0  # דולר ל-1,000 חיפושים, נוסף על הטוקנים

# Nano Banana 2: מחיר קבוע לתמונה לפי רזולוציה (מחירון fal). עריכה: אותו מחיר, לא אומת בנפרד.
NANO_PER_IMAGE = {"0.5K": 0.06, "1K": 0.08, "2K": 0.12, "4K": 0.16}
QUALITY_TO_NANO = {"low": "0.5K", "medium": "1K", "high": "2K", "xhigh": "4K"}

# GPT Image 2.5 (Flare = Sunburst): מחיר לתמונה 1024x1024 לפי איכות (טבלת fal, ספטמבר 2026).
# גדלים אחרים: הערכה לפי יחס הפיקסלים. עריכה: תוספת של כ-0.008$ לכל תמונת רפרנס 1024x1024 (הערת fal).
GPT_PER_IMAGE_1MP = {"low": 0.00588, "medium": 0.01317, "high": 0.05268, "xhigh": 0.09366, "max": 0.21072}
GPT_REF_INPUT = 0.008
_MP = 1024 * 1024


def _model_rates(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    m = model.lower()
    if m in ANTHROPIC_PER_MTOK:
        return ANTHROPIC_PER_MTOK[m]
    # גרסה עם סיומת תאריך או כינוי: התאמה לפי קידומת
    for key, rates in ANTHROPIC_PER_MTOK.items():
        if m.startswith(key):
            return rates
    return None


def estimate(entry: dict) -> dict:
    """מחזיר {"usd": float, "basis": "list"|"estimate", "note": str} לרשומת שימוש אחת."""
    step = entry.get("step")
    if step in ("write", "research", "prompt", "describe", "translate", "video_plan"):
        rates = _model_rates(entry.get("model"))
        inp = int(entry.get("input_tokens") or 0)
        out = int(entry.get("output_tokens") or 0)
        searches = int(entry.get("web_searches") or 0)
        if rates is None:
            return {"usd": 0.0, "basis": "estimate", "note": f"מודל לא במחירון: {entry.get('model')}"}
        usd = inp / 1e6 * rates[0] + out / 1e6 * rates[1] + searches / 1000.0 * WEB_SEARCH_PER_1000
        note = f"{inp:,} קלט + {out:,} פלט"
        if searches:
            note += f" + {searches} חיפושים"
        return {"usd": round(usd, 5), "basis": "list", "note": note}

    if step == "image":
        endpoint = str(entry.get("endpoint") or "")
        quality = str(entry.get("quality") or "medium")
        w, h = int(entry.get("width") or 0), int(entry.get("height") or 0)
        refs = int(entry.get("refs") or 0)
        edit = bool(entry.get("edit_mode"))
        if "nano-banana" in endpoint:
            res = QUALITY_TO_NANO.get(quality, "1K")
            usd = NANO_PER_IMAGE.get(res, 0.08)
            return {"usd": round(usd, 5), "basis": "estimate" if edit else "list",
                    "note": f"Nano Banana 2 {res}" + (" (עריכה, מחיר לא אומת)" if edit else "")}
        if "gpt-image" in endpoint:
            base = GPT_PER_IMAGE_1MP.get(quality, GPT_PER_IMAGE_1MP["medium"])
            ratio = (w * h / _MP) if (w and h) else 1.0
            usd = base * ratio + (GPT_REF_INPUT * refs if edit else 0.0)
            exact = abs(ratio - 1.0) < 1e-6 and not edit
            return {"usd": round(usd, 5), "basis": "list" if exact else "estimate",
                    "note": f"GPT Image 2.5 {quality} {w}x{h}" + (f" + {refs} רפרנס" if edit else "")}
        return {"usd": 0.0, "basis": "estimate", "note": f"מנוע לא במחירון: {endpoint}"}

    if step == "clip":
        # Higgsfield API: נספר בקרדיטים של החשבון; אין המרה מאומתת לדולר
        credits = entry.get("credits_est")
        return {"usd": 0.0, "basis": "estimate",
                "note": f"Higgsfield {entry.get('model')} {entry.get('duration')}s"
                        + (f", כ-{credits} קרדיטים" if credits else ", קרדיטים לפי חשבון Higgsfield")}
    if step == "assemble":
        return {"usd": 0.0, "basis": "list", "note": "ffmpeg מקומי, ללא עלות"}
    return {"usd": 0.0, "basis": "estimate", "note": "שלב לא מוכר"}


def priced(entry: dict) -> dict:
    """מוסיף לרשומה usd/basis/note ומחזיר אותה."""
    e = estimate(entry)
    entry["usd"] = e["usd"]
    entry["basis"] = e["basis"]
    entry["note"] = e["note"]
    entry["pricing_date"] = PRICING_DATE
    return entry


def total(entries) -> float:
    return round(sum(float(e.get("usd") or 0) for e in entries or []), 5)


def kind_of(step: str) -> str:
    if step == "image":
        return "fal"
    if step in ("clip", "assemble"):
        return "higgsfield"
    return "anthropic"
