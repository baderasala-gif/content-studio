# -*- coding: utf-8 -*-
"""
images.py - יצירת תמונות דרך fal.ai, על בסיס המנוע הקבוע C:\\dev\\tools\\generate_image.py.

המנוע הקבוע מספק: טעינת FAL_KEY, העלאת תמונות רפרנס, הורדה ושמירה.
כאן נוספת טבלת מודלים שכוללת גם את GPT Image 2.5 (Flare / Sunburst), שעדיין
אינו בקובץ המקור. שני המנועים רצים על אותו מפתח FAL.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import config

if str(config.FAL_IMAGE_TOOL.parent) not in sys.path:
    sys.path.insert(0, str(config.FAL_IMAGE_TOOL.parent))

try:
    from generate_image import (  # type: ignore
        GenerateImageError, download_images, load_fal_key, prepare_reference_urls,
    )
except Exception as _e:  # pragma: no cover
    raise RuntimeError(f"לא נמצא המנוע הקבוע generate_image.py ב-{config.FAL_IMAGE_TOOL}: {_e}")


class ImageError(RuntimeError):
    pass


ENGINES = {
    "nano-banana-2": {
        "label": "Nano Banana 2",
        "generate": "fal-ai/nano-banana-2",
        "edit": "fal-ai/nano-banana-2/edit",
        "max_refs": 14,
        "price_hint": "כ-0.08$ לתמונה ב-1K, 0.12$ ב-2K, 0.16$ ב-4K (fal.ai, 08.10.2026)",
    },
    "gpt-image-2.5-flare": {
        "label": "ChatGPT Image 2.5 Flare",
        "generate": "openai/gpt-image-2.5/flare/text-to-image",
        "edit": "openai/gpt-image-2.5/flare/edit",
        "max_refs": 16,
        "price_hint": "כ-0.01$ לתמונה ב-medium, כ-0.04$ ב-high, כ-0.08$ ב-xhigh (fal.ai, 08.10.2026)",
    },
    "gpt-image-2.5-sunburst": {
        "label": "ChatGPT Image 2.5 Sunburst",
        "generate": "openai/gpt-image-2.5/sunburst/text-to-image",
        "edit": "openai/gpt-image-2.5/sunburst/edit",
        "max_refs": 16,
        "price_hint": "אותו מחיר כמו Flare, איטי יותר ומדויק יותר בפרטים",
    },
}

RATIOS = ("1:1", "4:5", "16:9", "9:16")

# GPT Image 2.5: גדלים מותאמים (כפולות של 16, בטווח הפיקסלים המותר).
GPT_SIZES = {
    "1:1": {"width": 1024, "height": 1024},
    "4:5": {"width": 1024, "height": 1280},
    "16:9": {"width": 1536, "height": 864},
    "9:16": {"width": 864, "height": 1536},
}
# שלוש רמות בממשק: medium / high / xhigh. ברירת המחדל בממשק: high.
GPT_QUALITY = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
NANO_RESOLUTION = {"low": "0.5K", "medium": "1K", "high": "2K", "xhigh": "4K"}
QUALITIES = tuple(GPT_QUALITY)


def engines_public() -> list[dict]:
    return [{"key": k, "label": v["label"], "price_hint": v["price_hint"]} for k, v in ENGINES.items()]


_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def _slug(text: str, limit: int = 40) -> str:
    text = re.sub(r"[\\/:*?\"<>|#%&+;\n\r\t]+", " ", text or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = text[:limit].strip("-. ")
    if not text or text.upper().split(".")[0] in _RESERVED:
        return "תמונה"
    return text


def build_request(engine: str, prompt: str, ratio: str, quality: str, ref_urls: list[str],
                  output_format: str = "png") -> tuple[str, dict]:
    spec = ENGINES.get(engine)
    if not spec:
        raise ImageError(f"מנוע לא מוכר: {engine}")
    if ratio not in RATIOS:
        raise ImageError(f"יחס לא נתמך: {ratio}")
    quality = quality if quality in QUALITIES else "high"
    is_edit = bool(ref_urls)
    if is_edit and len(ref_urls) > spec["max_refs"]:
        raise ImageError(f"{spec['label']} מקבל עד {spec['max_refs']} תמונות רפרנס.")
    endpoint = spec["edit"] if is_edit else spec["generate"]
    args: dict = {"prompt": prompt, "num_images": 1, "output_format": output_format}
    if is_edit:
        args["image_urls"] = list(ref_urls)
    if engine.startswith("gpt-image"):
        args["quality"] = GPT_QUALITY[quality]
        args["image_size"] = dict(GPT_SIZES[ratio])
    else:
        args["aspect_ratio"] = ratio
        args["resolution"] = NANO_RESOLUTION[quality]
    return endpoint, args


def generate(*, prompt: str, engine: str, ratio: str, quality: str, refs: list[str],
             session: str, label: str, retries: int = 2) -> dict:
    """יוצר תמונה אחת ושומר אותה ב-output/<session>/. מחזיר נתיב ומידע."""
    import fal_client

    if not prompt or not prompt.strip():
        raise ImageError("חסר פרומפט לתמונה.")
    try:
        load_fal_key(str(config.BASE_DIR / ".env"))
    except GenerateImageError as e:
        raise ImageError(str(e)) from e

    try:
        ref_urls = prepare_reference_urls(refs or [])
    except GenerateImageError as e:
        raise ImageError(str(e)) from e

    endpoint, args = build_request(engine, prompt.strip(), ratio, quality, ref_urls)

    last_err: Exception | None = None
    result = None
    t0 = time.time()
    for attempt in range(retries + 1):
        try:
            result = fal_client.subscribe(endpoint, arguments=args, with_logs=False,
                                          client_timeout=300)
            break
        except fal_client.FalClientHTTPError as e:
            status = getattr(e, "status_code", None)
            body = getattr(e, "message", None) or str(e)
            if status in (400, 401, 402, 403, 404, 422):
                hint = ""
                if status == 401:
                    hint = " (בדוק את FAL_KEY בקובץ .env)"
                elif status == 402:
                    hint = " (יתרת fal.ai נגמרה)"
                elif status in (400, 422):
                    hint = " (פרמטר לא תקין או חסימת תוכן; שנה את הניסוח)"
                raise ImageError(f"fal.ai החזיר שגיאה {status}{hint}: {body}") from e
            last_err = e
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(3 * (attempt + 1))
    if result is None:
        raise ImageError(f"הקריאה ל-fal.ai נכשלה אחרי {retries + 1} ניסיונות: {last_err}")

    images = result.get("images") or []
    if not images:
        raise ImageError("fal.ai לא החזיר תמונה.")

    session_dir = (config.OUTPUT_DIR / _slug(session, 30)).resolve()
    if not session_dir.is_relative_to(config.OUTPUT_DIR.resolve()):
        raise ImageError("שם סשן לא תקין.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = session_dir / f"{_slug(label)}-{stamp}.png"
    try:
        saved = download_images(images, out, "png")
    except GenerateImageError as e:
        raise ImageError(str(e)) from e
    path = saved[0]
    rel = path.relative_to(config.OUTPUT_DIR).as_posix()
    info = images[0]
    width, height = info.get("width"), info.get("height")
    if not width or not height:
        try:
            from PIL import Image

            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            pass
    return {
        "path": str(path),
        "url": "/output/" + quote(rel),
        "filename": path.name,
        "width": width,
        "height": height,
        "endpoint": endpoint,
        "edit_mode": bool(ref_urls),
        "seconds": round(time.time() - t0, 1),
        "fal_url": info.get("url"),
    }
