# -*- coding: utf-8 -*-
"""
server.py - המנוע המקומי של Content Studio.

מגיש את דף הממשק, ומאחוריו מפעיל את הסקילים: כתיבת פוסט (post-writer), בניית
פרומפט (visual-maker) ויצירת תמונה (fal.ai). המפתחות נשארים כאן ולא מגיעים לדפדפן.

הפעלה:  python server.py --open
כתובת:   http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import socket
import sys
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import config  # noqa: E402

config.load_env()
config.ensure_dirs()

from engine import claude, history, images, pricing, video, visual, writer  # noqa: E402

_RUN_ROUTE = re.compile(r"^/api/runs/([^/]+)(?:/(post|prompt|delete|video-plan))?$")

MAX_BODY = 60 * 1024 * 1024
UPLOAD_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_uploads: dict[str, dict] = {}
_uploads_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ContentStudio/1.0"

    # ---------- עזרי תשובה ----------
    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # הדפדפן ניתק לפני שהתשובה נשלחה (למשל לשונית מושהית). התוצאה כבר נשמרה בריצה.
            log(f"הלקוח ניתק לפני קבלת התשובה ל-{self.path}; התוצאה שמורה בהיסטוריה.")

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _send_file(self, path: Path, cache: bool = False) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("הבקשה גדולה מדי.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("גוף הבקשה אינו JSON תקין.")
        if not isinstance(data, dict):
            raise ValueError("גוף הבקשה חייב להיות אובייקט.")
        return data

    def _safe_under(self, root: Path, rel: str) -> Path | None:
        rel = unquote(rel).replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            return None
        p = (root / rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            return None
        return p

    def _same_origin(self, require_header: bool = False) -> bool:
        """הגנה מפני CSRF: רק דפים מהכתובת המקומית של המנוע רשאים לפנות אליו."""
        port = self.server.server_address[1]
        allowed_hosts = {f"{config.HOST}:{port}", f"localhost:{port}"}
        allowed_origins = {f"http://{h}" for h in allowed_hosts}
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed_hosts:
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin and origin not in allowed_origins:
            return False
        if require_header and self.headers.get("X-Content-Studio") != "1":
            return False
        return True

    def log_message(self, fmt, *args):  # שקט בלוג, רק שגיאות
        if args and str(args[1]).startswith(("4", "5")):
            log(f"{self.command} {self.path} -> {args[1]}")

    # ---------- GET ----------
    def do_GET(self) -> None:
        if not self._same_origin():
            return self._send_error_json("הבקשה לא הגיעה מהכתובת המקומית של המנוע.", 403)
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self._send_file(config.WEB_DIR / "index.html")
        if path.startswith("/static/"):
            p = self._safe_under(config.WEB_DIR, path[len("/static/"):])
            return self._send_file(p) if p else self.send_error(HTTPStatus.NOT_FOUND)
        if path.startswith("/output/"):
            p = self._safe_under(config.OUTPUT_DIR, path[len("/output/"):])
            return self._send_file(p, cache=True) if p else self.send_error(HTTPStatus.NOT_FOUND)
        if path.startswith("/uploads/"):
            p = self._safe_under(config.UPLOAD_DIR, path[len("/uploads/"):])
            return self._send_file(p, cache=True) if p else self.send_error(HTTPStatus.NOT_FOUND)
        if path == "/api/health":
            st = config.status()
            st.update({
                "ok": True,
                "engines": images.engines_public(),
                "profiles": visual.PROFILES,
                "ratios": visual.RATIOS,
                "platforms": [{"key": k, "label": v["label"]} for k, v in writer.PLATFORMS.items()],
                "video": video.status(),
            })
            return self._send_json(st)
        if path == "/favicon.ico":
            return self._send_file(config.WEB_DIR / "favicon.svg", cache=True)
        if path == "/api/runs":
            return self._send_json({"ok": True, "runs": history.list_runs()})
        if path == "/api/costs":
            return self._send_json({"ok": True, "costs": history.costs_summary()})
        m = _RUN_ROUTE.match(path)
        if m and not m.group(2):
            try:
                return self._send_json({"ok": True, "run": history.load(m.group(1))})
            except history.HistoryError as e:
                return self._send_error_json(str(e), 404)
        self.send_error(HTTPStatus.NOT_FOUND)

    # ---------- POST ----------
    def do_POST(self) -> None:
        if not self._same_origin(require_header=True):
            return self._send_error_json("הבקשה לא הגיעה מדף Content Studio.", 403)
        path = urlparse(self.path).path
        routes = {
            "/api/write": self.api_write,
            "/api/upload-ref": self.api_upload_ref,
            "/api/visual-prompt": self.api_visual_prompt,
            "/api/visual-translate": self.api_visual_translate,
            "/api/image": self.api_image,
            "/api/runs": self.api_run_create,
            "/api/video/plan": self.api_video_plan,
            "/api/video/still": self.api_video_still,
            "/api/video/clip": self.api_video_clip,
            "/api/video/assemble": self.api_video_assemble,
        }
        fn = routes.get(path)
        run_match = _RUN_ROUTE.match(path) if not fn else None
        if not fn and not (run_match and run_match.group(2)):
            return self._send_error_json("נתיב לא קיים.", 404)
        try:
            body = self._read_json()
            if fn:
                result = fn(body)
            else:
                result = self.api_run_action(run_match.group(1), run_match.group(2), body)
            self._send_json({"ok": True, **result})
        except (claude.ClaudeError, images.ImageError, history.HistoryError, video.VideoError, ValueError) as e:
            log(f"שגיאה ב-{path}: {e}")
            self._send_error_json(str(e), 400)
        except Exception as e:  # noqa: BLE001
            log(f"שגיאה לא צפויה ב-{path}: {e}\n{traceback.format_exc()}")
            self._send_error_json(f"שגיאה לא צפויה במנוע: {e}", 500)

    # ---------- API: היסטוריה ----------
    def api_run_create(self, b: dict) -> dict:
        run = history.new_run(b)
        log(f"ריצה חדשה: {run['id']} ({run['brief']['topic'][:50]})")
        return {"run": run}

    def api_run_action(self, run_id: str, action: str, b: dict) -> dict:
        if action == "post":
            history.record_post_edit(run_id, str(b.get("text") or ""))
            return {"saved": True}
        if action == "prompt":
            items = b.get("items")
            if not isinstance(items, list):
                raise ValueError("חסרה רשימת פריטים.")
            history.record_prompt_edit(run_id, [{k: str(it.get(k) or "") for k in
                                                 ("title_he", "description_he", "prompt_en", "alt_he")}
                                                | {"index": int(it.get("index") or i + 1)}
                                                for i, it in enumerate(items) if isinstance(it, dict)])
            return {"saved": True}
        if action == "delete":
            target = history.trash(run_id)
            log(f"ריצה הועברה לאשפה: {run_id}")
            return {"trashed": target}
        if action == "video-plan":
            shots = b.get("shots")
            if not isinstance(shots, list):
                raise ValueError("חסרה רשימת שוטים.")
            clean = [{"index": int(s.get("index") or i + 1),
                      "caption_he": str(s.get("caption_he") or "")[:80],
                      "prompt_en": str(s.get("prompt_en") or ""),
                      "motion_en": str(s.get("motion_en") or "")}
                     for i, s in enumerate(shots) if isinstance(s, dict)]
            history.record_video_plan_edit(run_id, clean)
            return {"saved": True}
        raise ValueError("פעולה לא מוכרת.")

    # ---------- API: וידאו לטיקטוק ----------
    def _run_dir(self, run_id) -> Path:
        if not (run_id and history.valid_id(str(run_id))):
            raise ValueError("שלב הוידאו דורש ריצה שמורה (run_id).")
        return config.OUTPUT_DIR / str(run_id)

    def api_video_plan(self, b: dict) -> dict:
        text = str(b.get("post_text") or "")
        log("וידאו: תכנון שוטים")
        plan = video.plan_from_text(text, profile_key=str(b.get("profile") or "personal"))
        entries = history.record_video_plan(b.get("run_id"), plan=plan, model=plan.get("model"),
                                            usage=plan.get("usage") or {})
        out = {k: plan[k] for k in ("shots", "caption_text", "hashtags")}
        return {"plan": out, "cost": {"usd": pricing.total(entries), "entries": entries}}

    def api_video_still(self, b: dict) -> dict:
        run_id = b.get("run_id")
        prompt = str(b.get("prompt_en") or "").strip()
        if not prompt:
            raise ValueError("הפרומפט של השוט ריק.")
        engine = str(b.get("engine") or "gpt-image-2.5-flare")
        if engine not in images.ENGINES:
            raise ValueError("מנוע תמונות לא מוכר.")
        quality = str(b.get("quality") or "high")
        index = int(b.get("index") or 1)
        session = str(run_id) if (run_id and history.valid_id(str(run_id))) else datetime.now().strftime("%Y%m%d")
        log(f"וידאו: סטיל {index} ({engine}, {quality})")
        res = images.generate(prompt=prompt + "\n\nVertical 9:16 composition, subject centered, quiet top third, "
                              "no text, no captions, no logos, no watermark.",
                              engine=engine, ratio="9:16", quality=quality, refs=[], session=session,
                              label=f"שוט-{index}")
        entries = history.record_video_still(run_id, index=index, image=res,
                                             settings={"engine": engine, "quality": quality, "prompt_en": prompt, "refs": 0})
        return {"image": res, "cost": {"usd": pricing.total(entries), "entries": entries}}

    def api_video_clip(self, b: dict) -> dict:
        run_id = b.get("run_id")
        rd = self._run_dir(run_id)
        index = int(b.get("index") or 1)
        run = history.load(str(run_id))
        slot = ((run.get("video") or {}).get("shots") or {}).get(str(index)) or {}
        still = (slot.get("still") or {}).get("path")
        if not still or not Path(still).is_file():
            raise ValueError(f"לשוט {index} אין סטיל. צור סטילים קודם.")
        model = str(b.get("model") or video.DEFAULT_MODEL)
        duration = int(b.get("duration") or 5)
        motion = str(b.get("motion_en") or slot.get("motion_en") or "slow gentle camera push-in, subtle natural movement, same subject, same colors, no text")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log(f"וידאו: קליפ {index} ({model}, {duration}s)")
        clip = video.hf_clip(still_path=still, motion_en=motion, model=model, duration=duration,
                             out_dir=rd, label=f"קליפ-{index}-{stamp}")
        clip["url"] = "/output/" + quote(Path(clip["path"]).relative_to(config.OUTPUT_DIR).as_posix())
        credits = {"seedance-2.5": 7 * duration}.get(model)  # הערכה לפי 35 קרדיטים ל-5 שניות
        entries = history.record_video_clip(run_id, index=index, clip=clip,
                                            settings={"model": model, "duration": duration, "motion_en": motion,
                                                      "credits_est": credits})
        return {"clip": clip, "cost": {"usd": pricing.total(entries), "entries": entries}}

    def api_video_assemble(self, b: dict) -> dict:
        run_id = b.get("run_id")
        rd = self._run_dir(run_id)
        run = history.load(str(run_id))
        shots_map = ((run.get("video") or {}).get("shots") or {})
        shots = []
        for k in sorted(shots_map, key=lambda x: int(x)):
            s = shots_map[k]
            shots.append({"index": int(k), "caption_he": s.get("caption_he") or "",
                          "clip_path": (s.get("clip") or {}).get("path"),
                          "still_path": (s.get("still") or {}).get("path")})
        shots = [s for s in shots if s["clip_path"] or s["still_path"]]
        if not shots:
            raise ValueError("אין סטילים או קליפים להרכבה.")
        seconds = int(b.get("seconds_per_still") or 5)
        captions = bool(b.get("captions", True))
        font = str(b.get("font") or video.DEFAULT_FONT)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = rd / f"טיקטוק-{stamp}.mp4"
        log(f"וידאו: הרכבה של {len(shots)} שוטים (גופן {font})")
        final = video.assemble(shots=shots, out_path=out, seconds_per_still=seconds, captions=captions,
                               font_key=font)
        final["url"] = "/output/" + quote(out.relative_to(config.OUTPUT_DIR).as_posix())
        final["shots_with_clip"] = sum(1 for s in shots if s["clip_path"])
        final["shots_from_still"] = sum(1 for s in shots if not s["clip_path"])
        entries = history.record_video_final(run_id, final=final,
                                             settings={"seconds_per_still": seconds, "captions": captions, "font": font})
        return {"final": final, "cost": {"usd": pricing.total(entries), "entries": entries}}

    # ---------- API ----------
    def api_write(self, b: dict) -> dict:
        topic = str(b.get("topic") or "")
        content = str(b.get("content") or "")
        platform = str(b.get("platform") or "linkedin")
        if platform not in writer.PLATFORMS:
            raise ValueError("פלטפורמה לא מוכרת.")
        do_research = bool(b.get("research"))
        previous = str(b.get("previous") or "") or None
        research_result = None
        # שימוש חוזר במחקר קיים ב"צור מחדש", רק אם מצב מחקר עדיין דולק
        brief = (str(b.get("research_brief") or "") or None) if do_research else None
        if do_research and not brief:
            log(f"מחקר: {topic[:60]}")
            research_result = writer.research(topic, content)
            brief = research_result["brief"]
        log(f"כתיבה ({platform}): {topic[:60]}")
        t0 = datetime.now()
        res = writer.write_post(topic, content, platform, research_brief=brief, previous=previous)
        out = {"text": res["text"], "enforcement": res["enforcement"], "usage": res["usage"],
               "model": res["model"], "platform": platform}
        if research_result:
            out["research"] = research_result
        elif brief:
            out["research"] = {"brief": brief, "sources": b.get("research_sources") or [], "reused": True}
        entries = history.record_post(b.get("run_id"), text=res["text"], platform=platform,
                                      research=research_result or (out.get("research") if brief else None),
                                      enforcement=res["enforcement"], usage=res["usage"], model=res["model"],
                                      seconds=(datetime.now() - t0).total_seconds())
        out["cost"] = {"usd": pricing.total(entries), "entries": entries}
        return out

    def api_upload_ref(self, b: dict) -> dict:
        name = Path(str(b.get("name") or "ref.png")).name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXT:
            raise ValueError("תמונת רפרנס חייבת להיות PNG, JPG או WebP.")
        data = str(b.get("data") or "")
        if "," in data[:100]:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data, validate=False)
        except Exception:
            raise ValueError("קובץ הרפרנס לא נקרא.")
        if not raw or len(raw) > 25 * 1024 * 1024:
            raise ValueError("קובץ הרפרנס ריק או גדול מ-25MB.")
        ref_id = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w.\-֐-׿ ]+", "_", Path(name).stem)[:40] or "ref"
        path = config.UPLOAD_DIR / f"{ref_id}-{safe}{ext}"
        path.write_bytes(raw)
        description = None
        note = None
        cost_entries: list = []
        if config.get_key("anthropic") and bool(b.get("describe", True)):
            try:
                description, d_usage, d_model = visual.describe_reference(path)
                cost_entries = history.record_small_call(b.get("run_id"), "describe", usage=d_usage, model=d_model)
            except claude.ClaudeError as e:
                note = f"התיאור לא נוצר: {e}"
        else:
            note = "אין מפתח Anthropic, התמונה תשמש כרפרנס בלי תיאור."
        rec = {"id": ref_id, "name": name, "path": str(path), "description": description,
               "url": "/uploads/" + quote(path.name)}
        with _uploads_lock:
            _uploads[ref_id] = rec
        history.record_ref(b.get("run_id"), rec)
        log(f"רפרנס הועלה: {name}")
        return {"ref": {"id": ref_id, "name": name, "url": rec["url"],
                        "description": description, "note": note},
                "cost": {"usd": pricing.total(cost_entries), "entries": cost_entries}}

    def _refs(self, ids, run_id=None) -> list[dict]:
        """רפרנסים לפי מזהה: מהזיכרון של השרת, ואם לא (ריצה שנטענה מההיסטוריה) מקובץ הריצה."""
        out = []
        wanted = [str(i) for i in (ids or [])]
        if not wanted:
            return out
        from_run = {}
        if run_id and history.valid_id(str(run_id)):
            try:
                for r in history.load(str(run_id)).get("refs") or []:
                    if r.get("id") and r.get("path") and Path(r["path"]).is_file():
                        from_run[r["id"]] = r
            except history.HistoryError:
                pass
        with _uploads_lock:
            for i in wanted:
                r = _uploads.get(i) or from_run.get(i)
                if r:
                    out.append(r)
        return out

    def api_visual_prompt(self, b: dict) -> dict:
        mode = "carousel" if b.get("mode") == "carousel" else "single"
        engine = str(b.get("engine") or "nano-banana-2")
        if engine not in images.ENGINES:
            raise ValueError("מנוע תמונות לא מוכר.")
        ratio = str(b.get("ratio") or "4:5")
        if ratio not in images.RATIOS:
            raise ValueError("יחס תמונה לא נתמך.")
        refs = self._refs(b.get("ref_ids"), b.get("run_id"))
        log(f"פרומפט ({mode}): {str(b.get('topic') or '')[:60]}")
        t0 = datetime.now()
        data = visual.build_prompts(
            mode=mode, slides=int(b.get("slides") or 3), topic=str(b.get("topic") or ""),
            content=str(b.get("content") or ""), post_text=b.get("post_text"),
            platform=b.get("platform"), profile=str(b.get("profile") or "auto"), ratio=ratio,
            engine=engine, references=refs, previous=b.get("previous") or None,
            instructions=b.get("instructions"), text_in_image=bool(b.get("text_in_image")),
        )
        entries = history.record_prompt(
            b.get("run_id"), data=data, seconds=(datetime.now() - t0).total_seconds(),
            settings={"mode": mode, "slides": int(b.get("slides") or 3), "engine": engine,
                      "ratio": ratio, "profile": str(b.get("profile") or "auto"),
                      "instructions": b.get("instructions") or "",
                      "text_in_image": bool(b.get("text_in_image")),
                      "ref_ids": [r["id"] for r in refs]})
        return {"prompt": data, "cost": {"usd": pricing.total(entries), "entries": entries}}

    def api_visual_translate(self, b: dict) -> dict:
        desc = str(b.get("description_he") or "").strip()
        if not desc:
            raise ValueError("התיאור בעברית ריק.")
        text, t_usage, t_model = visual.translate_description(
            desc, ratio=str(b.get("ratio") or "4:5"), profile_name_he=b.get("profile_name_he"),
            prompt_en_before=b.get("prompt_en_before"),
        )
        entries = history.record_small_call(b.get("run_id"), "translate", usage=t_usage, model=t_model)
        return {"prompt_en": text, "cost": {"usd": pricing.total(entries), "entries": entries}}

    def api_image(self, b: dict) -> dict:
        prompt = str(b.get("prompt_en") or "").strip()
        if not prompt:
            raise ValueError("הפרומפט ריק.")
        engine = str(b.get("engine") or "nano-banana-2")
        if engine not in images.ENGINES:
            raise ValueError("מנוע תמונות לא מוכר.")
        ratio = str(b.get("ratio") or "4:5")
        quality = str(b.get("quality") or "medium")
        run_id = b.get("run_id")
        refs = [r["path"] for r in self._refs(b.get("ref_ids"), run_id)]
        # עקביות בקרוסלה: סלייד קודם כרפרנס (נתיב מקומי בתוך output בלבד)
        consist = b.get("consistency_path")
        if consist:
            p = self._safe_under(config.OUTPUT_DIR, str(consist).replace("/output/", "", 1))
            if p and p.is_file():
                refs.append(str(p))
        # כותרת בתוך התמונה: בתמונה חדשה, או כהוספה לגרסה נקייה קיימת (base_path, מצב עריכה)
        headline = visual.clean_headline(str(b.get("headline_he") or ""))
        variant = str(b.get("variant") or ("text" if headline else "plain"))
        headline_mode = "generate"
        base = b.get("base_path")
        if headline and base:
            p = self._safe_under(config.OUTPUT_DIR, str(base).replace("/output/", "", 1))
            if p and p.is_file():
                refs.append(str(p))
                headline_mode = "edit"
        final_prompt = prompt + (visual.headline_instruction(headline, headline_mode) if headline else "")
        # תמונות של ריצה נשמרות בתיקיית הריצה; בלי ריצה: תיקייה לפי תאריך
        if run_id and history.valid_id(str(run_id)):
            session = str(run_id)
        else:
            session = str(b.get("session") or datetime.now().strftime("%Y%m%d"))
        label = str(b.get("label") or "תמונה") + ("-נקי" if variant == "clean" else "")
        index = int(b.get("index") or 1)
        log(f"תמונה ({engine}, {ratio}, {quality}, refs={len(refs)}, {variant}): {label}")
        res = images.generate(prompt=final_prompt, engine=engine, ratio=ratio, quality=quality,
                              refs=refs, session=session, label=label)
        log(f"נשמרה: {res['path']} ({res['seconds']} ש')")
        entries = history.record_image(run_id, index=index, image=res,
                                       settings={"engine": engine, "ratio": ratio, "quality": quality,
                                                 "prompt_en": prompt, "consistency": bool(consist),
                                                 "refs": len(refs), "variant": variant,
                                                 "headline_he": headline, "headline_mode": headline_mode})
        res["variant"] = variant
        res["headline_he"] = headline
        return {"image": res, "cost": {"usd": pricing.total(entries), "entries": entries}}


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Content Studio engine")
    ap.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    ap.add_argument("--open", action="store_true", help="פתח את הדפדפן אחרי ההפעלה")
    args = ap.parse_args()
    url = f"http://{config.HOST}:{args.port}/"

    if port_in_use(config.HOST, args.port):
        log(f"המנוע כבר רץ בכתובת {url}")
        if args.open:
            webbrowser.open(url)
        return 0

    st = config.status()
    log("Content Studio")
    log(f"מודל Anthropic: {st['model']}")
    log(f"מפתח FAL: {'נמצא' if st['keys']['fal'] else 'חסר'} | מפתח Anthropic: "
        f"{'נמצא' if st['keys']['anthropic'] else 'חסר (הוסף ANTHROPIC_API_KEY לקובץ .env)'}")
    log(f"קובץ קול: {'נמצא' if st['sources']['voice'] else 'חסר'} | style.md: "
        f"{'נמצא' if st['sources']['visual_style'] else 'חסר'} | סקיל post-writer: "
        f"{'נמצא' if st['sources']['post_writer_skill'] else 'לא נמצא (עובדים לפי voice-and-style.md)'}")

    httpd = ThreadingHTTPServer((config.HOST, args.port), Handler)
    httpd.daemon_threads = True
    log(f"המנוע דולק: {url}  (לסגירה: Ctrl+C או סגירת החלון)")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("נסגר.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
