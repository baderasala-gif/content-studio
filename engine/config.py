# -*- coding: utf-8 -*-
"""
config.py - נתיבים, מפתחות וברירות מחדל של Content Studio.

המפתחות נקראים מקובץ .env בתיקיית הפרויקט. אם חסר שם מפתח, יש נפילה
לקובץ C:\\dev\\.env.local (המקור המרכזי של באדר) ואחריו למשתני הסביבה.
המפתחות לעולם לא נכתבים ללוג ולא נשלחים לדפדפן.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR = BASE_DIR / "uploads"
LOCAL_SKILLS_DIR = BASE_DIR / "skills"

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
MEMORY_DIR = CLAUDE_DIR / "memory"
VOICE_FILE = MEMORY_DIR / "voice-and-style.md"
IDENTITY_FILE = MEMORY_DIR / "identity-dna.md"
VISUAL_SKILL_DIR = CLAUDE_DIR / "skills" / "visual-maker"
VISUAL_SKILL_FILE = VISUAL_SKILL_DIR / "SKILL.md"
VISUAL_STYLE_FILE = VISUAL_SKILL_DIR / "style.md"

# הסקיל post-writer: נטען מהמיקום הראשון שקיים.
POST_WRITER_CANDIDATES = (
    CLAUDE_DIR / "skills" / "post-writer" / "SKILL.md",
    LOCAL_SKILLS_DIR / "post-writer" / "SKILL.md",
)

FAL_IMAGE_TOOL = Path(r"C:\dev\tools\generate_image.py")

ENV_FILES = (
    BASE_DIR / ".env",
    BASE_DIR / ".env.local",
    Path(r"C:\dev\.env.local"),
)

KEY_NAMES = {
    "fal": ("FAL_KEY", "FAL_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"


def load_env() -> None:
    """טוען מפתחות ל-os.environ בלי להדפיס אותם. הראשון שנמצא מנצח."""
    for path in ENV_FILES:
        if not path.is_file():
            continue
        try:
            values = dotenv_values(path, encoding="utf-8")
        except Exception:
            continue
        for name, value in values.items():
            value = (value or "").strip().strip('"').strip("'")
            if value and not os.environ.get(name):
                os.environ[name] = value


def get_key(kind: str) -> str:
    for name in KEY_NAMES[kind]:
        v = os.environ.get(name, "").strip()
        if v and not v.startswith("..."):
            return v
    return ""


def model_name() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL


def post_writer_skill_path() -> Path | None:
    for p in POST_WRITER_CANDIDATES:
        if p.is_file():
            return p
    return None


def status() -> dict:
    """מצב התצורה לתצוגה בממשק. לא כולל ערכי מפתחות."""
    return {
        "keys": {
            "fal": bool(get_key("fal")),
            "anthropic": bool(get_key("anthropic")),
        },
        "model": model_name(),
        "sources": {
            "voice": VOICE_FILE.is_file(),
            "identity": IDENTITY_FILE.is_file(),
            "visual_style": VISUAL_STYLE_FILE.is_file(),
            "visual_skill": VISUAL_SKILL_FILE.is_file(),
            "post_writer_skill": post_writer_skill_path() is not None,
            "image_tool": FAL_IMAGE_TOOL.is_file(),
        },
        "paths": {
            "output": str(OUTPUT_DIR),
            "uploads": str(UPLOAD_DIR),
            "env": str(BASE_DIR / ".env"),
        },
    }


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
