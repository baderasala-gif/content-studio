# -*- coding: utf-8 -*-
"""
visual.py - בניית פרומפטים לתמונה או לקרוסלה לפי הסקיל visual-maker.

מקור האמת: style.md של הסקיל (ליבה + פרופילי קהל), נקרא במלואו בכל הפעלה.
הפרומפט לתמונה נבנה באנגלית (כנדרש במנוע), ולצדו תיאור בעברית שבאדר קורא ועורך.
תמונות רפרנס מתוארות בעברית בעזרת Claude (vision) לפני בניית הפרומפט.
"""
from __future__ import annotations

from pathlib import Path

from . import claude, config

PROFILES = [
    {"key": "auto", "label": "זיהוי אוטומטי מהתוכן"},
    {"key": "staff", "label": "ארגון: צוות והדרכה פנימית (2.1)"},
    {"key": "residents", "label": "ארגון: דיירים (2.2)"},
    {"key": "families", "label": "ארגון: משפחות וקהילה (2.3)"},
    {"key": "external", "label": "ארגון: חיצוני ורשמי (2.4)"},
    {"key": "academic", "label": "אקדמי (2.5)"},
    {"key": "personal", "label": "אישי ולינקדאין (2.6)"},
    {"key": "ventures", "label": "יזמות ופרויקטים (2.7)"},
    {"key": "teaching", "label": "הוראה והרצאות לאנשי מקצוע (2.8)"},
]

RATIOS = [
    {"key": "4:5", "label": "4:5 פוסט (אנכי)"},
    {"key": "1:1", "label": "1:1 ריבוע"},
    {"key": "16:9", "label": "16:9 רוחבי"},
    {"key": "9:16", "label": "9:16 סטורי"},
]


def _read(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def describe_reference(path: str | Path) -> str:
    """תיאור בעברית של תמונת רפרנס: סגנון, אווירה, תאורה, קומפוזיציה, צבעוניות, תוכן."""
    system = (
        "אתה מתאר תמונות רפרנס עבור בניית פרומפט לתמונת AI. תאר בעברית, בחמישה עד שבעה משפטים קצרים: "
        "סגנון (צילום, איור, שטוח, תלת-ממד), אווירה, תאורה, קומפוזיציה, צבעוניות במילים (לא בקודי hex), "
        "ומה מופיע בתמונה (אובייקטים, סביבה, פעולה). אם יש אדם בתמונה, תאר תנוחה, גיל משוער ולבוש בלבד; "
        "אל תזהה אותו ואל תתאר תווי פנים. אם יש טקסט או לוגו, ציין שקיימים בלי להעתיק אותם. "
        "החזר את התיאור בלבד."
    )
    msg = {"role": "user", "content": [claude.image_block_from_file(path),
                                       {"type": "text", "text": "תאר את תמונת הרפרנס."}]}
    res = claude.run(system, [msg], max_tokens=2000, effort="low")
    return res["text"], res["usage"], res["model"]


def clean_headline(text: str) -> str:
    """כותרת לתמונה: שורה אחת, בלי אימוג'ים וקווים, עד 60 תווים, בלי פיסוק בסוף."""
    from . import textrules

    t, _ = textrules.enforce(text or "")
    t = " ".join(t.split())
    t = t.rstrip(".:;,!?")
    return t[:60]


def headline_instruction(headline: str, mode: str = "generate") -> str:
    """הוראת הטקסט שמצורפת לפרומפט. mode: generate (תמונה חדשה) או edit (הוספה לתמונה קיימת)."""
    h = headline.replace('"', "'")
    if mode == "edit":
        return (
            f"\n\nEdit: add a headline to this exact image and change nothing else. Keep every other element, "
            f"color, pose and composition identical. Place the headline in the quiet top third, centered, "
            f"as clean bold sans-serif Hebrew text, high contrast against the background, right-to-left, "
            f"with the exact spelling, character by character: \"{h}\". No other text, no logos, no watermark."
        )
    return (
        f"\n\nText in image: in the quiet top third, centered, render this exact Hebrew headline as clean bold "
        f"sans-serif text, high contrast against the background, right-to-left, with the exact spelling, "
        f"character by character: \"{h}\". No other text anywhere, no logos, no watermark."
    )


def _profile_hint(profile: str) -> str:
    if profile == "auto" or not profile:
        return ("בחר את הפרופיל המתאים ביותר מסעיף 2 של style.md לפי הקהל והמטרה שעולים מהחומר. "
                "פוסט אישי לרשתות חברתיות שייך בדרך כלל לפרופיל 'אישי ולינקדאין'. "
                "כשהפרופיל 'pending' בפלטה או בסגנון, השתמש בהשערה הראשונית שכתובה בו וציין זאת בהערה.")
    labels = {p["key"]: p["label"] for p in PROFILES}
    return f"באדר בחר את הפרופיל: {labels.get(profile, profile)}. עבוד לפיו."


def build_prompts(*, mode: str, slides: int, topic: str, content: str, post_text: str | None,
                  platform: str | None, profile: str, ratio: str, engine: str,
                  references: list[dict], previous: list[dict] | None = None,
                  instructions: str | None = None, text_in_image: bool = False) -> dict:
    style = _read(config.VISUAL_STYLE_FILE)
    if not style:
        raise claude.ClaudeError(f"קובץ הסגנון לא נמצא: {config.VISUAL_STYLE_FILE}")

    n = max(3, min(6, int(slides or 3))) if mode == "carousel" else 1
    engine_note = {
        "nano-banana-2": "Nano Banana 2 (מתאים לאווירה, צילום, איור ועריכה).",
        "gpt-image-2.5-flare": "GPT Image 2.5 Flare (מתאים לטקסט, אינפוגרפיקה, טיפוגרפיה ופרטים קטנים).",
        "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst (איטי יותר, דיוק גבוה בפרטים).",
    }.get(engine, engine)

    system = "\n".join([
        "אתה הסקיל visual-maker של באדר עאסלה. המשימה: לבנות פרומפט (או רצף פרומפטים לקרוסלה) "
        "לתמונת AI לפי קובץ הסגנון שלהלן. קרא את style.md במלואו ופעל לפי שלב 3 שבסקיל: "
        "ארבע שכבות בסדר הזה: הנושא, הפרופיל (פלטה במילים בלבד, בלי קודי hex), הליבה "
        "(ייצוג מכבד ובסוכנות, דיוק אנטומי וציוד, no logos, no brand marks, no text אלא אם התבקש טקסט), "
        "וה'לא' של הפרופיל מנוסח כחיוב כשאפשר.",
        "",
        "כללים נוספים:",
        "- הפרומפט לתמונה באנגלית, 60 עד 140 מילים, קונקרטי: סצנה, דמויות (גנריות, מבוגרים, בלי כיסויי ראש "
        "ובלי סמלים דתיים או עדתיים), פעולה, סביבה, תאורה, פלטה במילים, סגנון, קומפוזיציה ליחס התמונה.",
        "- לכל פרומפט כתוב גם תיאור בעברית (3 עד 5 משפטים) שאומר לבאדר מה ייראה בתמונה, כדי שיוכל לערוך.",
        "- קרוסלה: רצף שמספר סיפור על פני הסליידים. סלייד 1 מושך ומציג את הנושא, סליידים אמצעיים "
        "מפתחים נקודה אחת כל אחד, הסלייד האחרון סוגר. אותו סגנון, אותה פלטה, אותה דמות (אם יש) בכל הסליידים; "
        "כתוב זאת במפורש בכל פרומפט (consistent style, same character, same palette).",
        ("- באדר ביקש כותרת בתוך התמונה: לכל פריט כתוב גם headline_he, כותרת בעברית של עד 6 מילים שמסכמת "
         "את הסלייד, בקול ישיר ופשוט, בלי אימוג'ים ובלי סימן פיסוק בסוף. בפרומפט האנגלי השאר את השליש העליון "
         "של התמונה פנוי ושקט (רקע אחיד, בלי פרטים) לכותרת, ואל תכתוב בו 'no text' ואל תכניס אליו את הכותרת "
         "עצמה: המנוע מוסיף את הוראת הטקסט בנפרד."
         if text_in_image else
         "- טקסט בתוך התמונה רק אם באדר ביקש זאת במפורש בהוראות. אחרת 'no text, no captions, no watermark'."),
        "- אין להעתיק פנים או אנשים מתמונות רפרנס; הן משמשות לסגנון, אווירה וקומפוזיציה בלבד.",
        "- בחר מצב אור כשהפרופיל מגדיר כזה.",
        "",
        f"מנוע התמונות שנבחר: {engine_note} יחס התמונה: {ratio}.",
        _profile_hint(profile),
        "",
        "הפלט: JSON בלבד, בתוך בלוק ```json, במבנה:",
        '{"profile_key": "<staff|residents|families|external|academic|personal|ventures|teaching>", '
        '"profile_name_he": "...", "light_mode_he": "... או null", "summary_he": "שורת סיכום בעברית: פרופיל, אווירה, פלטה", '
        '"items": [{"index": 1, "title_he": "כותרת קצרה", "description_he": "תיאור בעברית", '
        '"prompt_en": "English prompt", "alt_he": "תיאור alt במשפט אחד"'
        + (', "headline_he": "כותרת עד 6 מילים"' if text_in_image else "") + '}], '
        '"notes_he": "הערות קצרות לבאדר, או null"}',
        f"מספר הפריטים ב-items: {n}.",
        "",
        "=== style.md ===",
        style,
    ])

    user_parts = [f"מצב: {'קרוסלה של ' + str(n) + ' סליידים' if mode == 'carousel' else 'תמונה בודדת'}.",
                  f"הנושא: {topic.strip() or '(לא נמסר)'}"]
    if content and content.strip():
        user_parts.append("התוכן שבאדר מסר:\n" + content.strip())
    if post_text and post_text.strip():
        user_parts.append(f"הפוסט שנכתב (הפרומפט מבוסס עליו; פלטפורמה: {platform or 'לא צוינה'}):\n"
                          + post_text.strip())
    else:
        user_parts.append("אין פוסט. הפרומפט מבוסס ישירות על הנושא והתוכן.")
    if references:
        lines = [f"- {r.get('name')}: {r.get('description') or '(ללא תיאור)'}" for r in references]
        user_parts.append("תמונות רפרנס (לסגנון ולקומפוזיציה; יישלחו למנוע במצב עריכה):\n" + "\n".join(lines))
    if instructions and instructions.strip():
        user_parts.append("הוראות נוספות של באדר לתמונה:\n" + instructions.strip())
    if previous:
        prev = "\n".join(f"{p.get('index', i + 1)}. {p.get('prompt_en', '')}" for i, p in enumerate(previous))
        user_parts.append("פרומפטים קודמים שבאדר ביקש להחליף. בנה כיוון חזותי שונה (קומפוזיציה, סצנה "
                          "או מטאפורה אחרת), באותו פרופיל:\n" + prev)
    user_parts.append("החזר JSON בלבד.")

    res = claude.run(system, [{"role": "user", "content": "\n\n".join(user_parts)}],
                     max_tokens=8000, effort="high")
    data = claude.extract_json(res["text"])
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        raise claude.ClaudeError("Claude החזיר מבנה לא צפוי. נסה שוב.")
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise claude.ClaudeError("Claude לא החזיר פרומפטים. נסה שוב.")
    items = items[:n]
    for i, it in enumerate(items):
        it["index"] = i + 1
        it.setdefault("title_he", f"פריט {i + 1}")
        it.setdefault("description_he", "")
        it.setdefault("prompt_en", "")
        it.setdefault("alt_he", "")
        it["headline_he"] = clean_headline(it.get("headline_he") or "") if text_in_image else ""
    data["items"] = items
    data["text_in_image"] = bool(text_in_image)
    data["usage"] = res["usage"]
    data["model"] = res["model"]
    return data


def translate_description(description_he: str, *, ratio: str, profile_name_he: str | None,
                          prompt_en_before: str | None) -> str:
    """באדר ערך את התיאור בעברית: בונים ממנו מחדש את הפרומפט באנגלית לפי כללי הליבה."""
    style = _read(config.VISUAL_STYLE_FILE)
    system = "\n".join([
        "אתה הסקיל visual-maker. באדר ערך את התיאור בעברית של תמונה. כתוב מחדש את הפרומפט באנגלית "
        "כך שישקף במדויק את התיאור המעודכן, ושמור על כללי הליבה של style.md (ייצוג מכבד, דיוק אנטומי, "
        "no logos, no text אלא אם נדרש, פלטה במילים). 60 עד 140 מילים. החזר את הפרומפט האנגלי בלבד.",
        f"יחס התמונה: {ratio}. פרופיל: {profile_name_he or 'לפי התיאור'}.",
        "", "=== style.md (סעיף הליבה רלוונטי במיוחד) ===", style,
    ])
    user = "התיאור המעודכן בעברית:\n" + description_he.strip()
    if prompt_en_before:
        user += "\n\nהפרומפט הקודם באנגלית (לשמירת מה שלא השתנה):\n" + prompt_en_before.strip()
    res = claude.run(system, [{"role": "user", "content": user}], max_tokens=2000, effort="medium")
    text = res["text"].strip().strip("`").strip()
    if not text:
        raise claude.ClaudeError("לא התקבל פרומפט מתורגם.")
    return text, res["usage"], res["model"]
