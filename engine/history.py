# -*- coding: utf-8 -*-
"""
history.py - היסטוריית ריצות של Content Studio.

כל ריצה (לחיצה על "התחל") מקבלת תיקייה משלה תחת output/<מזהה>/ ובה run.json
עם הבריף, גרסאות הטקסט, הפרומפטים, התמונות, הרפרנסים ורשומות שימוש (לכל קריאה
למודל או למנוע תמונות) עם הערכת עלות שמחושבת בזמן הרישום (pricing.py).
התמונות של הריצה נשמרות באותה תיקייה. מחיקה מעבירה את התיקייה ל-output/_trash/.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path

from . import config, pricing

RUN_FILE = "run.json"
TRASH_DIR = "_trash"
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_lock = threading.Lock()


class HistoryError(ValueError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def valid_id(run_id: str | None) -> bool:
    return bool(run_id) and bool(_RUN_ID_RE.match(str(run_id)))


def _dir(run_id: str) -> Path:
    if not valid_id(run_id):
        raise HistoryError("מזהה ריצה לא תקין.")
    return config.OUTPUT_DIR / run_id


def _path(run_id: str) -> Path:
    return _dir(run_id) / RUN_FILE


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, run: dict) -> None:
    run["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def new_run(brief: dict) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    run = {
        "id": run_id,
        "created_at": _now(),
        "updated_at": _now(),
        "brief": {
            "topic": str(brief.get("topic") or "")[:300],
            "content": str(brief.get("content") or "")[:20000],
            "mode": brief.get("mode") if brief.get("mode") in ("both", "post", "image") else "both",
            "platform": str(brief.get("platform") or "linkedin"),
            "research": bool(brief.get("research")),
        },
        "post": None,
        "prompt": None,
        "images": {},
        "refs": [],
        "usage": [],
    }
    with _lock:
        d = _dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        _write(d / RUN_FILE, run)
    return run


def load(run_id: str) -> dict:
    p = _path(run_id)
    if not p.is_file():
        raise HistoryError("הריצה לא נמצאה.")
    return _read(p)


def update(run_id: str, fn) -> dict:
    """קורא, מעדכן דרך fn(run) ושומר, תחת נעילה."""
    with _lock:
        p = _path(run_id)
        if not p.is_file():
            raise HistoryError("הריצה לא נמצאה.")
        run = _read(p)
        fn(run)
        _write(p, run)
        return run


def update_if(run_id: str | None, fn) -> None:
    """כמו update, אבל שקט כשאין מזהה ריצה (קריאות ישירות ל-API בלי ממשק)."""
    if not run_id:
        return
    try:
        update(str(run_id), fn)
    except HistoryError:
        pass


def run_cost(run: dict) -> float:
    return pricing.total(run.get("usage") or [])


def _summary(run: dict) -> dict:
    images = run.get("images") or {}
    first = None
    for k in sorted(images, key=lambda x: int(x)):
        cur = (images[k] or {}).get("current")
        if cur and cur.get("url"):
            first = cur["url"]
            break
    post = run.get("post") or {}
    return {
        "id": run["id"],
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "topic": (run.get("brief") or {}).get("topic") or "",
        "platform": (run.get("brief") or {}).get("platform"),
        "mode": (run.get("brief") or {}).get("mode"),
        "research": bool((run.get("brief") or {}).get("research")),
        "has_post": bool(post.get("current")),
        "post_versions": len(post.get("versions") or []),
        "has_prompt": bool(run.get("prompt")),
        "image_count": sum(1 for v in images.values() if (v or {}).get("current")),
        "thumbnail": first,
        "calls": len(run.get("usage") or []),
        "cost_usd": run_cost(run),
    }


def _iter_runs():
    if not config.OUTPUT_DIR.is_dir():
        return
    for d in config.OUTPUT_DIR.iterdir():
        if not d.is_dir() or d.name == TRASH_DIR or not valid_id(d.name):
            continue
        p = d / RUN_FILE
        if not p.is_file():
            continue
        try:
            yield _read(p)
        except Exception:
            continue


def list_runs(limit: int = 200) -> list[dict]:
    out = [_summary(r) for r in _iter_runs()]
    out.sort(key=lambda r: r["id"], reverse=True)
    return out[:limit]


def costs_summary() -> dict:
    """סיכום עלות: החודש הנוכחי וסך הכול, לפי ספק, מתוך run.json של כל הריצות (לא כולל אשפה)."""
    month = datetime.now().strftime("%Y-%m")
    agg = {"month": {"label": month, "usd": 0.0, "anthropic": 0.0, "fal": 0.0, "higgsfield": 0.0, "runs": 0, "calls": 0},
           "total": {"usd": 0.0, "anthropic": 0.0, "fal": 0.0, "higgsfield": 0.0, "runs": 0, "calls": 0}}
    for run in _iter_runs():
        usage = run.get("usage") or []
        buckets = ["total"] + (["month"] if str(run.get("created_at", "")).startswith(month) else [])
        for b in buckets:
            agg[b]["runs"] += 1
        for u in usage:
            usd = float(u.get("usd") or 0)
            kind = pricing.kind_of(u.get("step"))
            for b in buckets:
                agg[b]["usd"] += usd
                agg[b][kind] += usd
                agg[b]["calls"] += 1
    for b in agg.values():
        for k in ("usd", "anthropic", "fal", "higgsfield"):
            b[k] = round(b[k], 4)
    agg["pricing_date"] = pricing.PRICING_DATE
    agg["sources"] = pricing.SOURCES
    return agg


def trash(run_id: str) -> str:
    with _lock:
        d = _dir(run_id)
        if not d.is_dir():
            raise HistoryError("הריצה לא נמצאה.")
        tdir = config.OUTPUT_DIR / TRASH_DIR
        tdir.mkdir(parents=True, exist_ok=True)
        target = tdir / d.name
        if target.exists():
            target = tdir / f"{d.name}-{secrets.token_hex(2)}"
        shutil.move(str(d), str(target))
        return str(target)


# ---------- רישום תוצאות. כל פונקציה מחזירה את רשומות השימוש (עם עלות), גם בלי ריצה ----------
def _entry(step: str, **fields) -> dict:
    e = {"step": step, "at": _now()}
    e.update(fields)
    return pricing.priced(e)


def record_post(run_id, *, text: str, platform: str, research: dict | None, enforcement: dict,
                usage: dict, model: str, seconds: float) -> list[dict]:
    entries = [_entry("write", model=model, seconds=round(seconds, 1),
                      input_tokens=int((usage or {}).get("input_tokens") or 0),
                      output_tokens=int((usage or {}).get("output_tokens") or 0))]
    if research and research.get("usage"):
        u = research["usage"]
        entries.append(_entry("research", model=model,
                              input_tokens=int(u.get("input_tokens") or 0),
                              output_tokens=int(u.get("output_tokens") or 0),
                              web_searches=int(u.get("web_searches") or 0),
                              sources=len(research.get("sources") or [])))

    def fn(run):
        post = run.get("post") or {"versions": [], "current": None, "research": None, "edited": False}
        post["versions"].append({"text": text, "at": _now(), "platform": platform,
                                 "enforcement": enforcement, "model": model})
        post["current"] = text
        post["edited"] = False
        post["platform"] = platform
        if research:
            post["research"] = {"brief": research.get("brief"), "sources": research.get("sources") or []}
        run["post"] = post
        run["brief"]["platform"] = platform
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_post_edit(run_id: str, text: str) -> None:
    def fn(run):
        post = run.get("post") or {"versions": [], "current": None, "research": None}
        post["current"] = text
        post["edited"] = True
        run["post"] = post
    update(run_id, fn)


def record_prompt(run_id, *, data: dict, settings: dict, seconds: float) -> list[dict]:
    u = data.get("usage") or {}
    entries = [_entry("prompt", model=data.get("model"), seconds=round(seconds, 1),
                      input_tokens=int(u.get("input_tokens") or 0),
                      output_tokens=int(u.get("output_tokens") or 0))]

    def fn(run):
        pr = run.get("prompt") or {"versions": [], "current": None}
        clean = {k: v for k, v in data.items() if k != "usage"}
        pr["versions"].append({"at": _now(), "data": clean, "settings": settings})
        pr["current"] = clean
        pr["settings"] = settings
        run["prompt"] = pr
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_prompt_edit(run_id: str, items: list[dict]) -> None:
    def fn(run):
        pr = run.get("prompt") or {"versions": [], "current": {}}
        cur = dict(pr.get("current") or {})
        cur["items"] = items
        cur["edited"] = True
        pr["current"] = cur
        run["prompt"] = pr
    update(run_id, fn)


def record_image(run_id, *, index: int, image: dict, settings: dict) -> list[dict]:
    entries = [_entry("image", endpoint=image.get("endpoint"), seconds=image.get("seconds"),
                      quality=settings.get("quality"), ratio=settings.get("ratio"),
                      edit_mode=bool(image.get("edit_mode")), refs=int(settings.get("refs") or 0),
                      width=image.get("width"), height=image.get("height"))]

    def fn(run):
        key = str(index)
        slot = (run.get("images") or {}).get(key) or {"versions": [], "current": None}
        entry = {k: image.get(k) for k in ("url", "path", "filename", "width", "height", "endpoint",
                                            "edit_mode", "seconds")}
        entry["at"] = _now()
        entry["settings"] = settings
        entry["usd"] = entries[0]["usd"]
        if settings.get("variant") == "clean":
            # גרסה נקייה בלי טקסט: נשמרת לצד הגרסה הראשית, לא מחליפה אותה
            slot.setdefault("clean_versions", []).append(entry)
            slot["clean"] = entry
        else:
            slot["versions"].append(entry)
            slot["current"] = entry
        run.setdefault("images", {})[key] = slot
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_small_call(run_id, step: str, *, usage: dict, model: str) -> list[dict]:
    """קריאות קטנות: תיאור רפרנס (describe) ותרגום (translate)."""
    entries = [_entry(step, model=model,
                      input_tokens=int((usage or {}).get("input_tokens") or 0),
                      output_tokens=int((usage or {}).get("output_tokens") or 0))]

    def fn(run):
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


# ---------- וידאו לטיקטוק: run["video"] = {"plan", "shots": {"1": {...}}, "final"} ----------
def _video(run: dict) -> dict:
    v = run.get("video") or {"plan": None, "shots": {}, "final": None, "settings": {}}
    run["video"] = v
    return v


def record_video_plan(run_id, *, plan: dict, model: str | None, usage: dict) -> list[dict]:
    entries = [_entry("video_plan", model=model,
                      input_tokens=int((usage or {}).get("input_tokens") or 0),
                      output_tokens=int((usage or {}).get("output_tokens") or 0))]

    def fn(run):
        v = _video(run)
        v["plan"] = {k: plan[k] for k in ("shots", "caption_text", "hashtags") if k in plan}
        v["plan"]["at"] = _now()
        for s in plan.get("shots") or []:
            slot = v["shots"].setdefault(str(s["index"]), {"stills": [], "still": None, "clips": [], "clip": None})
            slot["caption_he"] = s.get("caption_he")
            slot["prompt_en"] = s.get("prompt_en")
            slot["motion_en"] = s.get("motion_en")
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_video_plan_edit(run_id: str, shots: list[dict]) -> None:
    def fn(run):
        v = _video(run)
        if v.get("plan") is None:
            v["plan"] = {"shots": [], "caption_text": "", "hashtags": ""}
        v["plan"]["shots"] = shots
        v["plan"]["edited"] = True
        for s in shots:
            slot = v["shots"].setdefault(str(s["index"]), {"stills": [], "still": None, "clips": [], "clip": None})
            slot["caption_he"] = s.get("caption_he")
            slot["prompt_en"] = s.get("prompt_en")
            slot["motion_en"] = s.get("motion_en")
    update(run_id, fn)


def record_video_still(run_id, *, index: int, image: dict, settings: dict) -> list[dict]:
    entries = [_entry("image", endpoint=image.get("endpoint"), seconds=image.get("seconds"),
                      quality=settings.get("quality"), ratio="9:16", edit_mode=bool(image.get("edit_mode")),
                      refs=int(settings.get("refs") or 0), width=image.get("width"), height=image.get("height"),
                      purpose="video_still")]

    def fn(run):
        v = _video(run)
        slot = v["shots"].setdefault(str(index), {"stills": [], "still": None, "clips": [], "clip": None})
        entry = {k: image.get(k) for k in ("url", "path", "filename", "width", "height", "endpoint", "seconds")}
        entry["at"] = _now(); entry["settings"] = settings; entry["usd"] = entries[0]["usd"]
        slot["stills"].append(entry); slot["still"] = entry
        slot["clip"] = None  # סטיל חדש מבטל קליפ ישן
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_video_clip(run_id, *, index: int, clip: dict, settings: dict) -> list[dict]:
    entries = [_entry("clip", model=clip.get("model"), duration=clip.get("duration"), seconds=clip.get("seconds"),
                      request_id=clip.get("request_id"), credits_est=settings.get("credits_est"))]

    def fn(run):
        v = _video(run)
        slot = v["shots"].setdefault(str(index), {"stills": [], "still": None, "clips": [], "clip": None})
        entry = {k: clip.get(k) for k in ("url", "path", "model", "duration", "seconds", "request_id")}
        entry["at"] = _now(); entry["settings"] = settings
        slot["clips"].append(entry); slot["clip"] = entry
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_video_final(run_id, *, final: dict, settings: dict) -> list[dict]:
    entries = [_entry("assemble", seconds=final.get("seconds"), duration=final.get("duration"))]

    def fn(run):
        v = _video(run)
        entry = {k: final.get(k) for k in ("url", "path", "duration", "segments", "seconds", "size_bytes")}
        entry["at"] = _now(); entry["settings"] = settings
        v.setdefault("finals", []).append(entry); v["final"] = entry
        v["settings"] = settings
        run["usage"].extend(entries)
    update_if(run_id, fn)
    return entries


def record_ref(run_id, ref: dict) -> None:
    def fn(run):
        run.setdefault("refs", []).append({k: ref.get(k) for k in ("id", "name", "url", "description", "path")})
    update_if(run_id, fn)
