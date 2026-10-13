# -*- coding: utf-8 -*-
"""
video.py - שלב הוידאו לטיקטוק: תכנון שוטים, הנפשת סטילים ב-Higgsfield, והרכבה עם כיתובים ב-ffmpeg.

שרשרת: חבילת הטיקטוק (טקסט מ-post-writer) → תוכנית שוטים (JSON דרך Claude) → סטיל 9:16 לכל שוט
(fal, המנוע הקיים) → קליפ לכל שוט (Higgsfield API, אם יש מפתח; אחרת תנועת Ken Burns על הסטיל)
→ סרטון אחד 1080x1920 עם כיתובי מסך בעברית (ffmpeg עם fribidi).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx

from . import claude, config

HF_BASE = "https://api.higgsfield.ai"
HF_MODELS = {
    "seedance-2.5": {
        "label": "Seedance 2.5 (Bytedance)",
        "path": "/bytedance/seedance-2.5/reference-to-video",
        "min_s": 4, "max_s": 30,
        "credits_hint": "כ-35 קרדיטים ל-5 שניות ב-720p (נמדד ב-MCP, 08.10.2026)",
    },
    "kling-o3": {
        "label": "Kling O3",
        "path": "/kling-video/o3/image-reference",
        "min_s": 3, "max_s": 15,
        "credits_hint": "לא נמדד",
    },
}
DEFAULT_MODEL = "seedance-2.5"
OUT_W, OUT_H, FPS = 1080, 1920, 30

# גופנים לכיתובים: נבדקים בתיקיית הגופנים של Windows ושל המשתמש. ברירת מחדל: Rubik Bold.
FONT_DIRS = (Path(r"C:\Windows\Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts")
FONTS = {
    "rubik": {"label": "Rubik Bold (מודרני, נקי)", "file": "Rubik-Bold.ttf"},
    "frank": {"label": "Frank Ruhl Hofshi Bold (עריכתי, סריפי)", "file": "FrankRuhlHofshi-Bold.otf"},
    "noto": {"label": "Noto Sans Hebrew Bold (ניטרלי)", "file": "NotoSansHebrew-Bold.ttf"},
    "arial": {"label": "Arial Bold (גיבוי)", "file": "arialbd.ttf"},
}
DEFAULT_FONT = "rubik"
FONT_BOLD = r"C:\Windows\Fonts\arialbd.ttf"  # גיבוי אחרון


def font_path(key: str | None) -> Path:
    key = key if key in FONTS else DEFAULT_FONT
    order = [key] + [k for k in FONTS if k != key]
    for k in order:
        for d in FONT_DIRS:
            p = d / FONTS[k]["file"]
            if p.is_file():
                return p
    return Path(FONT_BOLD)


def fonts_public() -> list[dict]:
    out = []
    for k, v in FONTS.items():
        if any((d / v["file"]).is_file() for d in FONT_DIRS):
            out.append({"key": k, "label": v["label"]})
    return out


class VideoError(RuntimeError):
    pass


# ---------- מפתחות וכלים ----------
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def hf_keys() -> tuple[str, str]:
    """
    מזהה המפתח של Higgsfield הוא UUID והסוד מחרוזת ארוכה. הקונסול מציג אותם לעיתים כזוג אחד
    'ID:SECRET'; אם הזוג הודבק בשדה הסוד (או בשדה המזהה), מפצלים אותו כאן.
    """
    kid = os.environ.get("HIGGSFIELD_KEY_ID", "").strip() or os.environ.get("HF_API_KEY_ID", "").strip()
    sec = os.environ.get("HIGGSFIELD_KEY_SECRET", "").strip() or os.environ.get("HF_API_KEY_SECRET", "").strip()
    for cand in (sec, kid):
        if ":" in cand:
            a, b = cand.split(":", 1)
            if _UUID_RE.match(a.strip()) and b.strip():
                return a.strip(), b.strip()
    return kid, sec


def has_hf() -> bool:
    kid, sec = hf_keys()
    return bool(kid and sec)


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def status() -> dict:
    return {"higgsfield": has_hf(), "ffmpeg": bool(ffmpeg_path()), "font": font_path(DEFAULT_FONT).is_file(),
            "fonts": fonts_public(), "default_font": DEFAULT_FONT,
            "models": [{"key": k, "label": v["label"], "credits_hint": v["credits_hint"]} for k, v in HF_MODELS.items()]}


# ---------- שלב 1: תוכנית שוטים ----------
PLAN_SYSTEM = (
    "אתה ממיר חבילת טיקטוק (טקסט) לתוכנית הפקה מובנית. החבילה מכילה: כיתובי מסך ממוספרים, בריף שוטים "
    "ממוספר עם פרומפט באנגלית לכל שוט, כיתוב לסרטון, והאשטגים. החזר JSON בלבד בתוך בלוק ```json במבנה:\n"
    '{"shots": [{"index": 1, "caption_he": "כיתוב המסך כפי שנכתב", "prompt_en": "פרומפט הסטיל באנגלית, '
    'vertical 9:16, בלי טקסט בתמונה", "motion_en": "משפט אחד באנגלית: תנועה עדינה וטבעית למצלמה ולדמות, '
    'same subject, same colors, no text"}], "caption_text": "הכיתוב לסרטון", "hashtags": "#..."}\n'
    "כללים: מספר השוטים = מספר הכיתובים (שוט לכל כיתוב). אם יש יותר כיתובים משוטים, שכפל שוט קרוב עם וריאציה. "
    "אל תשנה את נוסח הכיתובים. הוסף לכל prompt_en את המילים 'vertical 9:16 composition, subject centered, "
    "quiet top third' אם חסרות, ו-'no text, no captions, no logos'. motion_en: תנועה איטית ורגועה בלבד "
    "(slow push-in, gentle pan, subtle natural movement), בלי חיתוכים, בלי טקסט."
)


def _profile_style_block(profile_key: str = "personal") -> str:
    """סעיף הפרופיל מ-style.md של visual-maker, כדי שהסטילים לוידאו יהיו באותו אופי כמו התמונות."""
    try:
        style = config.VISUAL_STYLE_FILE.read_text(encoding="utf-8")
    except Exception:
        return ""
    heads = {"personal": "### 2.6", "staff": "### 2.1", "residents": "### 2.2", "families": "### 2.3",
             "external": "### 2.4", "academic": "### 2.5", "ventures": "### 2.7", "teaching": "### 2.8"}
    h = heads.get(profile_key, "### 2.6")
    i = style.find(h)
    if i < 0:
        return ""
    j = style.find("\n### ", i + 5)
    section = style[i: j if j > 0 else len(style)]
    core_i = style.find("### 1.1")
    core = style[core_i: style.find("### 1.3")] if core_i >= 0 else ""
    return core + "\n" + section


def plan_from_text(post_text: str, profile_key: str = "personal") -> dict:
    if not post_text or not post_text.strip():
        raise VideoError("אין טקסט טיקטוק לתכנון.")
    style_block = _profile_style_block(profile_key)
    system = PLAN_SYSTEM
    if style_block:
        system += (
            "\n\nסגנון חזותי מחייב לכל prompt_en: כתוב מחדש את פרומפט הסטיל כך שישקף את הפרופיל שלהלן "
            "(סגנון, אווירה, תאורה, פלטה במילים בלבד, 'כן' ו'לא', ייצוג מכבד ודיוק ציוד), תוך שמירה על "
            "התוכן של השוט. אותה פלטה ואותו סגנון בכל השוטים (consistent series).\n\n=== style.md (ליבה + פרופיל) ===\n"
            + style_block
        )
    res = claude.run(system, [{"role": "user", "content": "חבילת הטיקטוק:\n\n" + post_text.strip()}],
                     max_tokens=6000, effort="low")
    data = claude.extract_json(res["text"])
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list) or not data["shots"]:
        raise VideoError("לא התקבלה תוכנית שוטים.")
    shots = []
    for i, s in enumerate(data["shots"][:8]):
        shots.append({
            "index": i + 1,
            "caption_he": str(s.get("caption_he") or "").strip()[:80],
            "prompt_en": str(s.get("prompt_en") or "").strip(),
            "motion_en": str(s.get("motion_en") or "slow gentle camera push-in, subtle natural movement, same subject, same colors, no text").strip(),
        })
    return {"shots": shots, "caption_text": str(data.get("caption_text") or ""),
            "hashtags": str(data.get("hashtags") or ""), "usage": res["usage"], "model": res["model"]}


# ---------- שלב 3: קליפ ב-Higgsfield ----------
def _public_url_for(path: str | Path) -> str:
    """Higgsfield דורש URL ציבורי: מעלים את הסטיל לאחסון fal (אותו מפתח כמו התמונות)."""
    import sys
    if str(config.FAL_IMAGE_TOOL.parent) not in sys.path:
        sys.path.insert(0, str(config.FAL_IMAGE_TOOL.parent))
    from generate_image import load_fal_key  # type: ignore
    import fal_client

    load_fal_key(str(config.BASE_DIR / ".env"))
    p = Path(path)
    return fal_client.upload(p.read_bytes(), "image/png" if p.suffix.lower() == ".png" else "image/jpeg")


def hf_clip(*, still_path: str, motion_en: str, model: str, duration: int, out_dir: Path, label: str,
            poll_seconds: int = 5, timeout_seconds: int = 900) -> dict:
    if not has_hf():
        raise VideoError("חסר מפתח Higgsfield API. צור מפתח ב-console.higgsfield.ai והוסף לקובץ .env את "
                         "HIGGSFIELD_KEY_ID ו-HIGGSFIELD_KEY_SECRET.")
    spec = HF_MODELS.get(model)
    if not spec:
        raise VideoError(f"מודל וידאו לא מוכר: {model}")
    duration = max(spec["min_s"], min(spec["max_s"], int(duration or 5)))
    image_url = _public_url_for(still_path)
    kid, sec = hf_keys()
    headers = {"Authorization": f"Key {kid}:{sec}", "Content-Type": "application/json",
               "Idempotency-Key": uuid.uuid4().hex}
    if model == "seedance-2.5":
        body = {"prompt": motion_en, "image_urls": [image_url], "duration": duration,
                "aspect_ratio": "9:16", "resolution": "720p", "generate_audio": False}
    else:
        body = {"prompt": motion_en, "image_urls": [image_url], "duration": duration,
                "aspect_ratio": "9:16", "mode": "std", "sound": "off", "multi_shots": False}
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True) as http:
        r = http.post(HF_BASE + spec["path"], headers=headers, json=body)
        if r.status_code == 401:
            raise VideoError("Higgsfield דחה את המפתח (401). בדוק את HIGGSFIELD_KEY_ID/SECRET.")
        if r.status_code == 403 and "not_enough_credits" in r.text:
            raise VideoError("ל-API של Higgsfield יש יתרה נפרדת מהמנוי (pay-as-you-go), והיא ריקה. "
                             "טעינה ב-console.higgsfield.ai תחת Billing. עד אז הסרטון מורכב מזום על הסטילים.")
        if r.status_code >= 400:
            raise VideoError(f"Higgsfield החזיר שגיאה {r.status_code}: {r.text[:300]}")
        sub = r.json()
        status_url = sub.get("status_url")
        request_id = sub.get("request_id")
        if not status_url:
            raise VideoError(f"תשובה לא צפויה מ-Higgsfield: {sub}")
        while True:
            if time.time() - t0 > timeout_seconds:
                raise VideoError("Higgsfield לא סיים בזמן (15 דקות).")
            time.sleep(poll_seconds)
            s = http.get(status_url, headers=headers)
            if s.status_code >= 400:
                raise VideoError(f"בדיקת סטטוס נכשלה ({s.status_code}).")
            st = s.json()
            state = st.get("status")
            if state == "completed":
                url = (st.get("video") or {}).get("url")
                if not url:
                    raise VideoError("Higgsfield סיים בלי קובץ וידאו.")
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{label}.mp4"
                data = http.get(url).content
                out.write_bytes(data)
                return {"path": str(out), "request_id": request_id, "model": model, "duration": duration,
                        "seconds": round(time.time() - t0, 1), "source_url": url}
            if state in ("failed", "nsfw", "canceled"):
                raise VideoError(f"Higgsfield: {state}. {st.get('error') or ''}".strip())


# ---------- שלב 4: הרכבה ----------
def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise VideoError("ffmpeg נכשל: " + (p.stderr or "")[-600:])


CAPTION_MAX_CHARS = 22  # תווים לשורה בגופן 64px על רוחב 1080 עם שוליים; drawtext לא שובר שורות לבד


def wrap_caption(text: str, max_chars: int = CAPTION_MAX_CHARS) -> str:
    """שבירת כיתוב לשורות לפי מילים, כדי שלא ייחתך בצדי הפריים. עד 3 שורות."""
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])


def render_caption_png(text: str, out_png: Path, *, font_key: str | None = None, size: int = 64,
                       max_width: int = OUT_W - 2 * 72) -> Path | None:
    """
    כיתוב כתמונת PNG שקופה: שורות שבורות, סדר ביידי נכון (python-bidi, כי אין raqm), גופן נבחר,
    קופסה אחת מעוגלת מאחורי כל השורות (בלי מרווחים בין קופסאות), צל עדין.
    """
    from PIL import Image, ImageDraw, ImageFont
    from bidi.algorithm import get_display

    text = wrap_caption((text or "").strip())
    if not text:
        return None
    fp = font_path(font_key)
    font = ImageFont.truetype(str(fp), size)
    lines = [get_display(ln) for ln in text.split("\n")]
    pad_x, pad_y, gap = 40, 26, 10
    widths, heights = [], []
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    for ln in lines:
        box = tmp.textbbox((0, 0), ln, font=font)
        widths.append(box[2] - box[0]); heights.append(box[3] - box[1])
    line_h = max(heights) if heights else size
    box_w = min(max(widths) + 2 * pad_x, max_width + 2 * pad_x)
    box_h = len(lines) * line_h + (len(lines) - 1) * gap + 2 * pad_y
    img = Image.new("RGBA", (OUT_W, box_h + 24), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0 = (OUT_W - box_w) // 2
    # צל עדין ואז הקופסה
    draw.rounded_rectangle((x0 + 4, 10, x0 + box_w + 4, box_h + 10), radius=22, fill=(0, 0, 0, 70))
    draw.rounded_rectangle((x0, 4, x0 + box_w, box_h + 4), radius=22, fill=(20, 18, 16, 165))
    y = 4 + pad_y
    for ln, w, h in zip(lines, widths, heights):
        box = tmp.textbbox((0, 0), ln, font=font)
        x = (OUT_W - w) // 2 - box[0]
        draw.text((x, y - box[1]), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h + gap
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png


def assemble(*, shots: list[dict], out_path: Path, seconds_per_still: int = 5, captions: bool = True,
             font_key: str | None = None) -> dict:
    """
    shots: [{"index", "caption_he", "clip_path" | None, "still_path" | None}]
    קליפ קיים → מנורמל ל-1080x1920; אין קליפ → Ken Burns (zoompan) על הסטיל. כיתוב (PNG) לכל שוט.
    """
    ff = ffmpeg_path()
    if not ff:
        raise VideoError("ffmpeg לא נמצא במחשב. התקן ffmpeg והוסף ל-PATH.")
    if not shots:
        raise VideoError("אין שוטים להרכבה.")
    work = out_path.parent / "_work"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    t0 = time.time()
    cap_y = int(OUT_H * 0.10)
    for s in shots:
        idx = int(s["index"])
        seg = work / f"seg-{idx}.mp4"
        cap_png = None
        if captions and (s.get("caption_he") or "").strip():
            cap_png = render_caption_png(s["caption_he"], work / f"cap-{idx}.png", font_key=font_key)
        if s.get("clip_path") and Path(s["clip_path"]).is_file():
            base = (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
                    f"fps={FPS},format=yuv420p[v]")
            inputs = ["-i", s["clip_path"]]
            extra = ["-an"]
        elif s.get("still_path") and Path(s["still_path"]).is_file():
            frames = int(seconds_per_still * FPS)
            base = (f"[0:v]scale=2160:-2,zoompan=z='min(zoom+0.0008,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)':s={OUT_W}x{OUT_H}:fps={FPS},format=yuv420p[v]")
            inputs = ["-loop", "1", "-i", s["still_path"]]
            extra = ["-t", str(seconds_per_still)]  # אפשרות פלט: בלעדיה הלולאה אינסופית
        else:
            raise VideoError(f"לשוט {idx} אין קליפ ואין סטיל.")
        if cap_png:
            fc = base + f";[v][1:v]overlay=(W-w)/2:{cap_y}:format=auto,format=yuv420p[out]"
            cmd = [ff, "-v", "error", "-y", *inputs, "-i", str(cap_png), "-filter_complex", fc,
                   "-map", "[out]", *extra, "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(seg)]
        else:
            cmd = [ff, "-v", "error", "-y", *inputs, "-filter_complex", base, "-map", "[v]", *extra,
                   "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(seg)]
        _run(cmd)
        parts.append(seg)
    lst = work / "concat.txt"
    lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
          "-movflags", "+faststart", str(out_path)])
    # משך
    dur = None
    try:
        probe = shutil.which("ffprobe")
        if probe:
            q = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out_path)],
                               capture_output=True, text=True)
            dur = round(float(json.loads(q.stdout)["format"]["duration"]), 1)
    except Exception:
        pass
    return {"path": str(out_path), "duration": dur, "segments": len(parts), "seconds": round(time.time() - t0, 1),
            "size_bytes": out_path.stat().st_size}
