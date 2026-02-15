"""Конфигурация приложения"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Support PyInstaller frozen exe: .env lives next to the exe
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# ── Gemini (image description only) ─────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

AVAILABLE_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
]

# ── Yandex Cloud (RAG: search index) ───────────────────
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "")
YC_API_KEY = os.getenv("YC_API_KEY", "")

# ── Retry ──────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds

# ── UI ──────────────────────────────────────────────────
MAX_IMAGE_PREVIEW = (400, 400)

SUPPORTED_DOC_FORMATS = [
    ("Документы", "*.pdf *.docx *.txt"),
    ("PDF файлы", "*.pdf"),
    ("Word документы", "*.docx"),
    ("Текстовые файлы", "*.txt"),
]

SUPPORTED_IMAGE_FORMATS = [
    ("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
    ("PNG", "*.png"),
    ("JPEG", "*.jpg *.jpeg"),
]
