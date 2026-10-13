# -*- coding: utf-8 -*-
"""
claude.py - שכבת גישה אחת ל-Claude (Anthropic) לכל חלקי המנוע.

כל הקריאות עוברות כאן: ניהול המפתח, סטרימינג (למניעת timeout), טיפול ב-pause_turn
של כלי שרת (חיפוש אינטרנט), סירוב (refusal) ושגיאות API בהודעות קריאות בעברית.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import config


class ClaudeError(RuntimeError):
    """שגיאה מבוקרת עם הודעה קריאה למשתמש."""


_client = None


def client():
    global _client
    if _client is None:
        import anthropic

        key = config.get_key("anthropic")
        if not key:
            raise ClaudeError(
                "חסר מפתח ANTHROPIC_API_KEY. הוסף אותו ל-C:\\dev\\.env.local (או לקובץ .env בתיקיית Content Studio) והפעל מחדש."
            )
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _blocks_to_params(blocks) -> list[dict]:
    out = []
    for b in blocks:
        if hasattr(b, "model_dump"):
            out.append(b.model_dump(exclude_none=True))
        else:
            out.append(b)
    return out


def _translate_error(e: Exception) -> ClaudeError:
    import anthropic

    if isinstance(e, anthropic.AuthenticationError):
        return ClaudeError("מפתח Anthropic לא תקין. בדוק את ANTHROPIC_API_KEY ב-C:\\dev\\.env.local או בקובץ .env.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return ClaudeError("למפתח Anthropic אין הרשאה לפעולה הזו.")
    if isinstance(e, anthropic.NotFoundError):
        return ClaudeError(f"המודל '{config.model_name()}' לא נמצא. בדוק את ANTHROPIC_MODEL.")
    if isinstance(e, anthropic.RateLimitError):
        return ClaudeError("Anthropic החזיר מגבלת קצב (429). המתן דקה ונסה שוב.")
    if isinstance(e, anthropic.BadRequestError):
        return ClaudeError(f"בקשה לא תקינה ל-Anthropic: {getattr(e, 'message', e)}")
    if isinstance(e, anthropic.APIStatusError):
        return ClaudeError(f"Anthropic החזיר שגיאה {e.status_code}: {getattr(e, 'message', e)}")
    if isinstance(e, anthropic.APIConnectionError):
        return ClaudeError("אין חיבור ל-Anthropic. בדוק את חיבור האינטרנט.")
    return ClaudeError(f"שגיאה לא צפויה מול Anthropic: {e}")


def run(
    system: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    max_tokens: int = 16000,
    effort: str = "high",
    max_continuations: int = 6,
) -> dict:
    """
    מריץ קריאה אחת (עם המשכים אוטומטיים אם כלי שרת השהה את התור).
    מחזיר: {"text": str, "blocks": [dict], "stop_reason": str, "usage": dict, "model": str}
    """
    import anthropic

    c = client()
    model = config.model_name()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=list(messages),
        output_config={"effort": effort},
    )
    if tools:
        kwargs["tools"] = tools

    base_messages = list(messages)
    all_blocks: list[dict] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}
    final = None

    for _ in range(max_continuations):
        try:
            with c.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
        except anthropic.APIError as e:
            raise _translate_error(e) from e
        except Exception as e:  # רשת וכו'
            raise _translate_error(e) from e

        blocks = _blocks_to_params(final.content)
        all_blocks.extend(blocks)
        try:
            usage_total["input_tokens"] += int(final.usage.input_tokens or 0)
            usage_total["output_tokens"] += int(final.usage.output_tokens or 0)
            stu = getattr(final.usage, "server_tool_use", None)
            if stu is not None:
                usage_total["web_searches"] += int(getattr(stu, "web_search_requests", 0) or 0)
        except Exception:
            pass

        if final.stop_reason == "pause_turn":
            # כלי שרת השהה את התור: שולחים את השיחה עם כל מה שהמודל הפיק עד כה,
            # והשרת ממשיך מאותה נקודה (בלי הודעת משתמש נוספת).
            kwargs["messages"] = base_messages + [{"role": "assistant", "content": list(all_blocks)}]
            continue
        break

    if final is None:
        raise ClaudeError("לא התקבלה תשובה מ-Claude.")

    if final.stop_reason == "pause_turn":
        raise ClaudeError("המחקר לא הסתיים אחרי כמה המשכים. נסה שוב או צמצם את הנושא.")
    if final.stop_reason == "model_context_window_exceeded":
        raise ClaudeError("התוכן ארוך מדי לחלון ההקשר של המודל. קצר את התוכן ונסה שוב.")
    if final.stop_reason == "refusal":
        details = getattr(final, "stop_details", None)
        why = getattr(details, "explanation", "") if details else ""
        raise ClaudeError("Claude סירב לבקשה. " + (why or "נסה לנסח את הנושא או התוכן אחרת."))
    if final.stop_reason == "max_tokens":
        raise ClaudeError("התשובה נקטעה (max_tokens). קצר את התוכן או נסה שוב.")

    # בלוקי טקסט עם ציטוטים הם קטעים של אותה פסקה, לכן מחברים בלי שורה חדשה
    text = "".join(b.get("text", "") for b in all_blocks if b.get("type") == "text").strip()
    return {
        "text": text,
        "blocks": all_blocks,
        "stop_reason": final.stop_reason,
        "usage": usage_total,
        "model": model,
    }


def extract_json(text: str) -> Any:
    """מחלץ אובייקט JSON מתשובה (עם או בלי בלוק קוד)."""
    if not text:
        raise ClaudeError("Claude החזיר תשובה ריקה.")
    m = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, flags=re.S)
    candidates = [m.group(1)] if m else []
    # ניסיון שני: אובייקט ומערך כמועמדים נפרדים
    for open_c, close_c in (("{", "}"), ("[", "]")):
        first, last = text.find(open_c), text.rfind(close_c)
        if 0 <= first < last:
            candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise ClaudeError("לא הצלחתי לקרוא את ה-JSON שחזר מ-Claude. נסה שוב.")


def image_block_from_file(path) -> dict:
    """בלוק תמונה (base64) לקריאת vision."""
    import base64
    import mimetypes
    from pathlib import Path

    p = Path(path)
    media = mimetypes.guess_type(p.name)[0] or "image/png"
    if media not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        media = "image/png"
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
