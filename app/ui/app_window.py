"""
AI Рекламный Цензор — главное окно.

Redesign: Yandex Translate dark style.
  - Full-page settings with big rounded cards, two-column layout
  - Info blocks with API setup instructions
  - File list with instant display, loading spinners, delete icons
  - Bigger text, no unnecessary scrolling
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
from PIL import Image

from app.config import (
    AVAILABLE_MODELS,
    BASE_DIR,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_IMAGE_PREVIEW,
    SUPPORTED_DOC_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    YC_API_KEY,
    YC_FOLDER_ID,
)
from app.ui.theme import COLORS, FONTS, SIZES

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ─── Helpers ─────────────────────────────────────────────

def _card(parent, **kw):
    defaults = dict(
        fg_color=COLORS["bg_secondary"],
        corner_radius=SIZES["radius_lg"],
        border_width=0,
    )
    defaults.update(kw)
    return ctk.CTkFrame(parent, **defaults)


def _info_block(parent, text, pad_x=0):
    """Blue-ish info hint block like Yandex help panels."""
    fr = ctk.CTkFrame(parent, fg_color=COLORS["bg_info"],
                      corner_radius=SIZES["radius_sm"])
    fr.pack(fill="x", padx=pad_x, pady=(0, 14))
    ctk.CTkLabel(fr, text=text, font=FONTS["small"],
                 text_color=COLORS["text_link"], anchor="w",
                 justify="left", wraplength=480).pack(padx=14, pady=10)
    return fr


def _sep(parent):
    s = ctk.CTkFrame(parent, height=1, fg_color=COLORS["border_light"])
    s.pack(fill="x", padx=16, pady=8)
    return s


# ── Context menu (right-click) for any widget ────────────

class _ContextMenu:
    """Universal right-click Copy / Paste / Cut / Select All menu."""

    def __init__(self, widget):
        self.w = widget
        self.menu = tk.Menu(widget, tearoff=0,
                            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                            activebackground=COLORS["accent"],
                            activeforeground="#ffffff",
                            font=("Segoe UI", 10))
        self.is_textbox = isinstance(widget, ctk.CTkTextbox)
        self._build()
        widget.bind("<Button-3>", self._show)
        self._bind_shortcuts()

    def _build(self):
        self.menu.add_command(label="Вырезать", accelerator="Ctrl+X", command=self._cut)
        self.menu.add_command(label="Копировать", accelerator="Ctrl+C", command=self._copy)
        self.menu.add_command(label="Вставить", accelerator="Ctrl+V", command=self._paste)
        self.menu.add_separator()
        self.menu.add_command(label="Выделить всё", accelerator="Ctrl+A", command=self._select_all)

    def _show(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _get_sel(self):
        if self.is_textbox:
            try:
                return self.w.get("sel.first", "sel.last")
            except tk.TclError:
                return ""
        else:
            try:
                return self.w.selection_get()
            except tk.TclError:
                return ""

    def _copy(self, e=None):
        txt = self._get_sel()
        if txt:
            self.w.clipboard_clear()
            self.w.clipboard_append(txt)
        return "break"

    def _cut(self, e=None):
        self._copy()
        if self.is_textbox:
            try:
                self.w.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
        else:
            try:
                self.w.delete("sel.first", "sel.last")
            except Exception:
                pass
        return "break"

    def _paste(self, e=None):
        try:
            txt = self.w.clipboard_get()
        except tk.TclError:
            return "break"
        if self.is_textbox:
            try:
                self.w.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            self.w.insert("insert", txt)
        else:
            try:
                self.w.delete("sel.first", "sel.last")
            except Exception:
                pass
            self.w.insert("insert", txt)
        return "break"

    def _select_all(self, e=None):
        if self.is_textbox:
            self.w.tag_add("sel", "1.0", "end-1c")
        else:
            self.w.select_range(0, "end")
            self.w.icursor("end")
        return "break"

    def _bind_shortcuts(self):
        for seq, fn in [
            ("<Control-v>", self._paste), ("<Control-V>", self._paste),
            ("<Control-c>", self._copy), ("<Control-C>", self._copy),
            ("<Control-x>", self._cut), ("<Control-X>", self._cut),
            ("<Control-a>", self._select_all), ("<Control-A>", self._select_all),
        ]:
            self.w.bind(seq, fn)


# ═════════════════════════════════════════════════════════
#  MAIN APP
# ═════════════════════════════════════════════════════════

class CensorApp(ctk.CTk):
    WIDTH = 1360
    HEIGHT = 900

    def __init__(self):
        super().__init__()
        self.title("AI Рекламный Цензор")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(1080, 720)
        self.configure(fg_color=COLORS["bg_primary"])

        icon_path = BASE_DIR / "assets" / "icon.ico"
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        # ── State ────────────────────────────────────────
        self.api_key: str = GEMINI_API_KEY
        self.yc_folder_id: str = YC_FOLDER_ID
        self.yc_api_key: str = YC_API_KEY
        self.rag_engine = None
        self.loaded_docs: list[str] = []
        self.current_image_path: Optional[str] = None
        self._img_ref = None
        self._is_analyzing = False
        self._stop_event = threading.Event()
        self.selected_model: str = GEMINI_MODEL
        self._doc_cards: dict[str, ctk.CTkFrame] = {}  # name -> card widget

        self._build_welcome()
        self._build_workspace()
        self._show_welcome()

    # ══════════════════════════════════════════════════════
    #  Navigation
    # ══════════════════════════════════════════════════════

    def _show_welcome(self):
        self.workspace_fr.pack_forget()
        self.welcome_fr.pack(fill="both", expand=True)
        self.api_entry.focus_set()

    def _show_workspace(self):
        self.welcome_fr.pack_forget()
        self.workspace_fr.pack(fill="both", expand=True)

    # ══════════════════════════════════════════════════════
    #  SETTINGS SCREEN (full-page, two columns)
    # ══════════════════════════════════════════════════════

    def _build_welcome(self):
        self.welcome_fr = ctk.CTkFrame(self, fg_color=COLORS["bg_primary"])

        # ── Top bar ──────────────────────────────────────
        top = ctk.CTkFrame(self.welcome_fr, height=56,
                           fg_color=COLORS["bg_secondary"], corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkLabel(top, text="  🛡️  Рекламный Цензор",
                     font=FONTS["heading"],
                     text_color=COLORS["accent"]).pack(side="left", padx=16)
        ctk.CTkLabel(top, text="Настройки",
                     font=FONTS["body"],
                     text_color=COLORS["text_secondary"]).pack(side="left", padx=(4, 0))

        # ── Main body — fills the window, NO scroll ──────
        body = ctk.CTkFrame(self.welcome_fr, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(2, weight=0)

        # ── Left column: API keys ────────────────────────
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10), pady=0)

        self._build_gemini_card(left)
        self._build_yandex_card(left)

        # ── Right column: Documents ──────────────────────
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(10, 0), pady=0)

        self._build_docs_card(right)

        # ── Bottom row: status + start button ────────────
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        self.welcome_status = ctk.CTkLabel(bottom, text="",
                                           font=FONTS["caption"],
                                           text_color=COLORS["text_secondary"])
        self.welcome_status.pack(side="left", padx=(4, 20))

        self.welcome_progress = ctk.CTkProgressBar(
            bottom, width=200, height=3, fg_color=COLORS["border"],
            progress_color=COLORS["accent"], corner_radius=2,
            mode="indeterminate",
        )
        # hidden by default — shown during operations

        self.start_btn = ctk.CTkButton(
            bottom, text="Начать работу", font=FONTS["heading"],
            height=SIZES["btn_h_lg"], width=260,
            corner_radius=SIZES["radius_md"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._start_work,
        )
        self.start_btn.pack(side="right")

    # ── Gemini API card ──────────────────────────────────

    def _build_gemini_card(self, parent):
        card = _card(parent)
        card.pack(fill="x", pady=(0, 14))

        # Header
        ctk.CTkLabel(card, text="Gemini API", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w"
                     ).pack(fill="x", padx=SIZES["pad_xl"], pady=(SIZES["pad_xl"], 4))
        ctk.CTkLabel(card, text="Используется только для описания изображений",
                     font=FONTS["small"], text_color=COLORS["text_secondary"],
                     anchor="w").pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))

        # API Key entry
        ctk.CTkLabel(card, text="API Key:", font=FONTS["caption_bold"],
                     text_color=COLORS["text_secondary"], anchor="w"
                     ).pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 4))
        self.api_entry = ctk.CTkEntry(
            card, placeholder_text="Вставьте ключ Gemini…",
            font=FONTS["body"], height=SIZES["entry_h"], show="•",
            fg_color=COLORS["bg_input"], border_color=COLORS["border_light"],
            border_width=1, text_color=COLORS["text_primary"],
            corner_radius=SIZES["radius_sm"],
        )
        self.api_entry.pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))
        _ContextMenu(self.api_entry)
        if self.api_key:
            self.api_entry.insert(0, self.api_key)

        # Model selector
        model_row = ctk.CTkFrame(card, fg_color="transparent")
        model_row.pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))
        ctk.CTkLabel(model_row, text="Модель (для изображений):",
                     font=FONTS["caption"],
                     text_color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.model_var = ctk.StringVar(value=self.selected_model)
        self.model_dropdown = ctk.CTkOptionMenu(
            model_row, values=AVAILABLE_MODELS, variable=self.model_var,
            font=FONTS["caption"], height=32, corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["bg_tertiary"], button_color=COLORS["border_light"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_secondary"],
            dropdown_hover_color=COLORS["bg_tertiary"],
            text_color=COLORS["text_primary"], command=self._on_model_change,
        )
        self.model_dropdown.pack(side="left", fill="x", expand=True)

        # Buttons row
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 6))

        self._api_visible = False
        self.toggle_vis_btn = ctk.CTkButton(
            btn_row, text="👁 Показать", width=110, height=34,
            font=FONTS["caption"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._toggle_key_visibility,
        )
        self.toggle_vis_btn.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="Проверить", width=110, height=34,
            font=FONTS["caption_bold"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._test_api_key,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            btn_row, text="Сохранить", width=110, height=34,
            font=FONTS["caption_bold"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._save_api_key,
        ).pack(side="right")

        # Info block
        _info_block(card,
                    "💡 Получите API-ключ на ai.google.dev → «Get API key».\n"
                    "Бесплатный тариф: 15 запросов/мин.\n"
                    "Рекомендуемая модель: gemini-2.5-flash (баланс скорости и качества).",
                    pad_x=SIZES["pad_xl"])

        # Bottom padding
        ctk.CTkFrame(card, height=4, fg_color="transparent").pack()

    # ── Yandex Cloud card ────────────────────────────────

    def _build_yandex_card(self, parent):
        card = _card(parent)
        card.pack(fill="both", expand=True, pady=0)

        ctk.CTkLabel(card, text="Yandex Cloud", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w"
                     ).pack(fill="x", padx=SIZES["pad_xl"], pady=(SIZES["pad_xl"], 4))
        ctk.CTkLabel(card, text="RAG-анализ: поиск по базе знаний + YandexGPT",
                     font=FONTS["small"], text_color=COLORS["text_secondary"],
                     anchor="w").pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))

        # Folder ID
        ctk.CTkLabel(card, text="Folder ID:", font=FONTS["caption_bold"],
                     text_color=COLORS["text_secondary"], anchor="w"
                     ).pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 4))
        self.yc_folder_entry = ctk.CTkEntry(
            card, placeholder_text="b1g...",
            font=FONTS["body"], height=SIZES["entry_h"],
            fg_color=COLORS["bg_input"], border_color=COLORS["border_light"],
            border_width=1, text_color=COLORS["text_primary"],
            corner_radius=SIZES["radius_sm"],
        )
        self.yc_folder_entry.pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))
        _ContextMenu(self.yc_folder_entry)
        if self.yc_folder_id:
            self.yc_folder_entry.insert(0, self.yc_folder_id)

        # API Key
        ctk.CTkLabel(card, text="API Key:", font=FONTS["caption_bold"],
                     text_color=COLORS["text_secondary"], anchor="w"
                     ).pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 4))
        self.yc_key_entry = ctk.CTkEntry(
            card, placeholder_text="AQVN...",
            font=FONTS["body"], height=SIZES["entry_h"], show="•",
            fg_color=COLORS["bg_input"], border_color=COLORS["border_light"],
            border_width=1, text_color=COLORS["text_primary"],
            corner_radius=SIZES["radius_sm"],
        )
        self.yc_key_entry.pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))
        _ContextMenu(self.yc_key_entry)
        if self.yc_api_key:
            self.yc_key_entry.insert(0, self.yc_api_key)

        # Test button
        ctk.CTkButton(
            card, text="Проверить подключение", height=34,
            font=FONTS["caption_bold"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._test_yandex_connection,
        ).pack(anchor="w", padx=SIZES["pad_xl"], pady=(0, 10))

        # Info block
        _info_block(card,
                    "💡 Yandex Cloud Console → console.yandex.cloud\n"
                    "1. Создайте сервисный аккаунт с ролями:\n"
                    "   ai.editor, ai.assistants.editor\n"
                    "2. Создайте API-ключ для этого аккаунта\n"
                    "3. Folder ID — на главной странице каталога",
                    pad_x=SIZES["pad_xl"])

    # ── Documents card ───────────────────────────────────

    def _build_docs_card(self, parent):
        card = _card(parent)
        card.pack(fill="both", expand=True)

        # Header
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=SIZES["pad_xl"], pady=(SIZES["pad_xl"], 4))
        ctk.CTkLabel(hdr, text="База знаний", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w").pack(side="left")
        self.welcome_doc_count = ctk.CTkLabel(
            hdr, text="0", font=FONTS["small_bold"],
            text_color=COLORS["text_tertiary"],
            width=28, height=22, corner_radius=11,
            fg_color=COLORS["bg_tertiary"])
        self.welcome_doc_count.pack(side="right")

        ctk.CTkLabel(card, text="Загрузите нормативные акты и регламенты.\nФорматы: PDF, DOCX, TXT",
                     font=FONTS["caption"], text_color=COLORS["text_secondary"],
                     anchor="w", justify="left").pack(fill="x", padx=SIZES["pad_xl"], pady=(0, 10))

        # File list — scrollable
        self.welcome_doc_list = ctk.CTkScrollableFrame(
            card, fg_color=COLORS["bg_input"],
            corner_radius=SIZES["radius_sm"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["border_light"],
        )
        self.welcome_doc_list.pack(fill="both", expand=True,
                                   padx=SIZES["pad_xl"], pady=(0, 10))

        self.welcome_empty_lbl = ctk.CTkLabel(
            self.welcome_doc_list,
            text="Нет загруженных документов",
            font=FONTS["caption"], text_color=COLORS["text_tertiary"],
        )
        self.welcome_empty_lbl.pack(pady=30)

        # Upload button
        ctk.CTkButton(
            card, text="📁  Выбрать файлы…", font=FONTS["body"],
            height=SIZES["btn_h"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_primary"], command=self._upload_docs_welcome,
        ).pack(fill="x", padx=SIZES["pad_xl"], pady=(0, SIZES["pad_xl"]))

    # ── Welcome handlers ─────────────────────────────────

    def _on_model_change(self, model_name: str):
        self.selected_model = model_name
        if self.rag_engine:
            self.rag_engine.set_model(model_name)
        self.welcome_status.configure(text=f"Модель: {model_name}",
                                      text_color=COLORS["text_secondary"])

    def _toggle_key_visibility(self):
        self._api_visible = not self._api_visible
        if self._api_visible:
            self.api_entry.configure(show="")
            self.toggle_vis_btn.configure(text="🔒 Скрыть")
        else:
            self.api_entry.configure(show="•")
            self.toggle_vis_btn.configure(text="👁 Показать")

    def _save_api_key(self):
        key = self.api_entry.get().strip()
        if not key:
            messagebox.showwarning("Ошибка", "Введите Gemini API-ключ.")
            return
        self.api_key = key
        self.yc_folder_id = self.yc_folder_entry.get().strip()
        self.yc_api_key = self.yc_key_entry.get().strip()
        env_lines = [f"GEMINI_API_KEY={key}"]
        if self.yc_folder_id:
            env_lines.append(f"YC_FOLDER_ID={self.yc_folder_id}")
        if self.yc_api_key:
            env_lines.append(f"YC_API_KEY={self.yc_api_key}")
        env_lines.append(f"GEMINI_MODEL={self.model_var.get()}")
        (BASE_DIR / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        self.welcome_status.configure(text="✓  Настройки сохранены",
                                      text_color=COLORS["green"])

    def _test_api_key(self):
        key = self.api_entry.get().strip()
        if not key:
            messagebox.showwarning("Ошибка", "Введите API-ключ.")
            return
        self.welcome_status.configure(text="Проверяю ключ…", text_color=COLORS["orange"])
        self.welcome_progress.pack(side="left", padx=(10, 0), before=self.start_btn)
        self.welcome_progress.start()

        def worker():
            import google.generativeai as genai
            genai.configure(api_key=key)
            results = []
            try:
                model = genai.GenerativeModel(self.selected_model)
                resp = model.generate_content("Ответь одним словом: привет")
                if resp and resp.text:
                    results.append(f"✓ Текст ({self.selected_model})")
                else:
                    results.append("✗ Текст: пустой ответ")
            except Exception as e:
                results.append(f"✗ Текст: {str(e)[:80]}")
            try:
                from PIL import Image as PILImage
                test_img = PILImage.new("RGB", (10, 10), color=(200, 50, 50))
                model = genai.GenerativeModel(self.selected_model)
                resp = model.generate_content(["Что изображено? Одно слово.", test_img])
                if resp and resp.text:
                    results.append("✓ Изображения")
                else:
                    results.append("✗ Изображения: пустой ответ")
            except Exception as e:
                results.append(f"✗ Изображения: {str(e)[:80]}")

            ok = all(r.startswith("✓") for r in results)
            summary = "  |  ".join(results)
            color = COLORS["green"] if ok else COLORS["red"]
            def done():
                self.welcome_progress.stop()
                self.welcome_progress.pack_forget()
                self.welcome_status.configure(text=summary, text_color=color)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _test_yandex_connection(self):
        folder_id = self.yc_folder_entry.get().strip()
        api_key = self.yc_key_entry.get().strip()
        if not folder_id or not api_key:
            messagebox.showwarning("Ошибка", "Введите Folder ID и API Key.")
            return
        self.welcome_status.configure(text="Проверяю Yandex Cloud…", text_color=COLORS["orange"])
        self.welcome_progress.pack(side="left", padx=(10, 0), before=self.start_btn)
        self.welcome_progress.start()

        def worker():
            from app.rag_engine import YandexRAG
            try:
                yc = YandexRAG(folder_id=folder_id, api_key=api_key)
                result = yc.test_connection()
            except Exception as e:
                result = str(e)[:200]

            def done():
                self.welcome_progress.stop()
                self.welcome_progress.pack_forget()
                if result == "ok":
                    self.welcome_status.configure(
                        text="✓  Yandex Cloud: подключение успешно",
                        text_color=COLORS["green"],
                    )
                else:
                    short = result.split("\n")[0] if "\n" in result else result
                    self.welcome_status.configure(
                        text=f"✗  {short[:80]}",
                        text_color=COLORS["red"],
                    )
                    messagebox.showerror("Yandex Cloud — ошибка подключения", result)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _upload_docs_welcome(self):
        paths = filedialog.askopenfilenames(title="Выберите документы",
                                            filetypes=SUPPORTED_DOC_FORMATS)
        if paths:
            self._process_documents(list(paths), welcome=True)

    def _start_work(self):
        key = self.api_entry.get().strip()
        if not key:
            messagebox.showwarning("Ошибка", "Введите Gemini API-ключ.")
            return
        self.api_key = key
        self.yc_folder_id = self.yc_folder_entry.get().strip()
        self.yc_api_key = self.yc_key_entry.get().strip()
        if not self.yc_folder_id or not self.yc_api_key:
            messagebox.showwarning("Ошибка", "Введите Yandex Cloud Folder ID и API Key.")
            return
        self._save_api_key()
        self._init_rag()
        self._load_existing_docs()
        self._show_workspace()

    def _init_rag(self):
        from app.rag_engine import RAGEngine
        try:
            self.rag_engine = RAGEngine(
                api_key=self.api_key,
                model_name=self.selected_model,
                yc_folder_id=self.yc_folder_id,
                yc_api_key=self.yc_api_key,
            )
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось инициализировать:\n{e}")

    # ══════════════════════════════════════════════════════
    #  WORKSPACE SCREEN
    # ══════════════════════════════════════════════════════

    def _build_workspace(self):
        self.workspace_fr = ctk.CTkFrame(self, fg_color=COLORS["bg_primary"])

        # ── Top bar ──────────────────────────────────────
        top = ctk.CTkFrame(self.workspace_fr, height=56,
                           fg_color=COLORS["bg_secondary"], corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkLabel(top, text="  🛡️  Рекламный Цензор", font=FONTS["heading"],
                     text_color=COLORS["accent"]).pack(side="left", padx=16)

        self.ws_status_lbl = ctk.CTkLabel(top, text="", font=FONTS["caption"],
                                          text_color=COLORS["text_secondary"])
        self.ws_status_lbl.pack(side="left", padx=20)

        ctk.CTkButton(
            top, text="⚙  Настройки", width=110, height=34,
            font=FONTS["caption_bold"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._show_welcome,
        ).pack(side="right", padx=16)

        # ── Body ─────────────────────────────────────────
        body = ctk.CTkFrame(self.workspace_fr, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_columnconfigure(0, weight=0, minsize=SIZES["sidebar_w"])
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_sidebar(body)

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=0)
        right.grid_rowconfigure(3, weight=4)
        right.grid_columnconfigure(0, weight=1)

        self._build_input_panel(right)
        self._build_action_bar(right)
        self._build_risk_indicator(right)
        self._build_results_panel(right)

    # ── Sidebar ──────────────────────────────────────────

    def _build_sidebar(self, parent):
        sb = _card(parent)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.pack_propagate(False)
        sb.configure(width=SIZES["sidebar_w"])

        hdr = ctk.CTkFrame(sb, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(16, 0))
        ctk.CTkLabel(hdr, text="Документы", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w").pack(side="left")
        self.doc_count_lbl = ctk.CTkLabel(hdr, text="0", font=FONTS["small_bold"],
                                          text_color=COLORS["text_tertiary"],
                                          width=28, height=22, corner_radius=11,
                                          fg_color=COLORS["bg_tertiary"])
        self.doc_count_lbl.pack(side="right")

        _sep(sb)

        self.doc_list_fr = ctk.CTkScrollableFrame(
            sb, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["border_light"],
        )
        self.doc_list_fr.pack(fill="both", expand=True, padx=6, pady=0)

        self.empty_docs_lbl = ctk.CTkLabel(
            self.doc_list_fr,
            text="Пока пусто.\nДобавьте документы\nдля анализа.",
            font=FONTS["caption"], text_color=COLORS["text_tertiary"], justify="center",
        )
        self.empty_docs_lbl.pack(pady=40)

        # Loading status
        self.sidebar_load_fr = ctk.CTkFrame(sb, fg_color="transparent")
        self.sidebar_load_fr.pack(fill="x", padx=16, pady=(4, 0))
        self.sidebar_load_lbl = ctk.CTkLabel(
            self.sidebar_load_fr, text="", font=FONTS["small"],
            text_color=COLORS["orange"], anchor="w",
        )
        self.sidebar_load_lbl.pack(fill="x")
        self.sidebar_progress = ctk.CTkProgressBar(
            self.sidebar_load_fr, height=3, fg_color=COLORS["border"],
            progress_color=COLORS["accent"], corner_radius=2,
            mode="indeterminate",
        )
        self.sidebar_load_fr.pack_forget()

        btns = ctk.CTkFrame(sb, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(8, 16))
        self.add_docs_btn = ctk.CTkButton(
            btns, text="📁  Добавить…", font=FONTS["body_small"],
            height=SIZES["btn_h"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._add_documents,
        )
        self.add_docs_btn.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(
            btns, text="Очистить всё", font=FONTS["body_small"],
            height=SIZES["btn_h"], corner_radius=SIZES["radius_sm"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._clear_all_docs,
        ).pack(fill="x")

    # ── Input panel ──────────────────────────────────────

    def _build_input_panel(self, parent):
        card = _card(parent)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Текст объявления", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=(20, 10), pady=(18, 6))

        self.text_input = ctk.CTkTextbox(
            card, font=FONTS["body"], fg_color=COLORS["bg_input"],
            text_color=COLORS["text_primary"], border_width=1,
            border_color=COLORS["border_light"], corner_radius=SIZES["radius_sm"],
            wrap="word",
        )
        self.text_input.grid(row=1, column=0, sticky="nsew", padx=(20, 10), pady=(0, 18))
        _ContextMenu(self.text_input)

        ctk.CTkLabel(card, text="Изображение", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w"
                     ).grid(row=0, column=1, sticky="w", padx=(10, 20), pady=(18, 6))

        img_box = ctk.CTkFrame(card, fg_color=COLORS["bg_input"],
                               corner_radius=SIZES["radius_sm"],
                               border_width=1, border_color=COLORS["border_light"])
        img_box.grid(row=1, column=1, sticky="nsew", padx=(10, 20), pady=(0, 18))

        self.img_label = ctk.CTkLabel(
            img_box, text="Нажмите «Выбрать»\nчтобы загрузить\nизображение",
            font=FONTS["body"], text_color=COLORS["text_tertiary"],
            justify="center", compound="top",
        )
        self.img_label.pack(fill="both", expand=True, padx=10, pady=10)

        self.img_name_lbl = ctk.CTkLabel(img_box, text="", font=FONTS["small"],
                                         text_color=COLORS["text_secondary"])
        self.img_name_lbl.pack(padx=10, pady=(0, 2))

        img_btns = ctk.CTkFrame(img_box, fg_color="transparent")
        img_btns.pack(fill="x", padx=10, pady=(0, 10))

        self.sel_img_btn = ctk.CTkButton(
            img_btns, text="Выбрать…", height=34, font=FONTS["body_small"],
            corner_radius=SIZES["radius_sm"], fg_color=COLORS["btn_secondary"],
            hover_color=COLORS["btn_secondary_hover"], text_color=COLORS["text_primary"],
            command=self._select_image,
        )
        self.sel_img_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.paste_img_btn = ctk.CTkButton(
            img_btns, text="📋 Вставить", height=34, font=FONTS["body_small"],
            corner_radius=SIZES["radius_sm"], fg_color=COLORS["btn_secondary"],
            hover_color=COLORS["btn_secondary_hover"], text_color=COLORS["text_primary"],
            command=self._paste_image,
        )
        self.paste_img_btn.pack(side="left", fill="x", expand=True, padx=(4, 4))

        self.del_img_btn = ctk.CTkButton(
            img_btns, text="Убрать", height=34, font=FONTS["body_small"],
            corner_radius=SIZES["radius_sm"], fg_color=COLORS["btn_danger"],
            hover_color=COLORS["btn_danger_hover"], state="disabled",
            command=self._remove_image,
        )
        self.del_img_btn.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # Ctrl+V on image box pastes from clipboard
        img_box.bind("<Control-v>", self._paste_image)
        img_box.bind("<Control-V>", self._paste_image)
        self.img_label.bind("<Control-v>", self._paste_image)
        self.img_label.bind("<Control-V>", self._paste_image)

    # ── Action bar ───────────────────────────────────────

    def _build_action_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=0)
        bar.grid_columnconfigure(2, weight=0)

        self.analyze_btn = ctk.CTkButton(
            bar, text="Проверить материал", font=FONTS["heading"],
            height=SIZES["btn_h_lg"], corner_radius=SIZES["radius_md"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._run_analysis,
        )
        self.analyze_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            bar, text="Стоп", width=90, font=FONTS["body_medium"],
            height=SIZES["btn_h_lg"], corner_radius=SIZES["radius_md"],
            fg_color=COLORS["btn_danger"], hover_color=COLORS["btn_danger_hover"],
            state="disabled", command=self._stop_analysis,
        )
        self.stop_btn.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.clear_result_btn = ctk.CTkButton(
            bar, text="Очистить", width=90, font=FONTS["body_medium"],
            height=SIZES["btn_h_lg"], corner_radius=SIZES["radius_md"],
            fg_color=COLORS["btn_secondary"], hover_color=COLORS["btn_secondary_hover"],
            text_color=COLORS["text_secondary"], command=self._clear_result,
        )
        self.clear_result_btn.grid(row=0, column=2, sticky="e")

        self.analysis_progress = ctk.CTkProgressBar(
            bar, height=3, fg_color=COLORS["border"], progress_color=COLORS["accent"],
            corner_radius=2, mode="indeterminate",
        )

    # ── Risk indicator ───────────────────────────────────

    def _build_risk_indicator(self, parent):
        ri = ctk.CTkFrame(parent, fg_color=COLORS["bg_secondary"],
                          corner_radius=SIZES["radius_md"], height=56)
        ri.grid(row=2, column=0, sticky="ew", pady=(4, 4))
        ri.grid_columnconfigure(0, weight=0)
        ri.grid_columnconfigure(1, weight=1)
        ri.grid_columnconfigure(2, weight=0)
        self._risk_frame = ri
        ri.grid_remove()

        self.risk_pct_lbl = ctk.CTkLabel(
            ri, text="—", font=("Segoe UI", 22, "bold"),
            text_color=COLORS["text_tertiary"], width=70,
        )
        self.risk_pct_lbl.grid(row=0, column=0, padx=(18, 8), pady=10)

        self.risk_bar = ctk.CTkProgressBar(
            ri, height=12, corner_radius=6,
            fg_color=COLORS["bg_tertiary"], progress_color=COLORS["green"],
            mode="determinate",
        )
        self.risk_bar.grid(row=0, column=1, sticky="ew", pady=10)
        self.risk_bar.set(0)

        self.risk_label = ctk.CTkLabel(
            ri, text="", font=FONTS["caption_bold"],
            text_color=COLORS["text_secondary"], width=140,
        )
        self.risk_label.grid(row=0, column=2, padx=(8, 18), pady=10)

    # ── Results panel ────────────────────────────────────

    def _build_results_panel(self, parent):
        card = _card(parent)
        card.grid(row=3, column=0, sticky="nsew")

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Результат проверки", font=FONTS["subheading"],
                     text_color=COLORS["text_primary"], anchor="w").pack(side="left")
        self.result_status_lbl = ctk.CTkLabel(hdr, text="", font=FONTS["caption_bold"],
                                              text_color=COLORS["text_tertiary"])
        self.result_status_lbl.pack(side="right")

        _sep(card)

        self.results_text = ctk.CTkTextbox(
            card, font=FONTS["body"], fg_color=COLORS["bg_input"],
            text_color=COLORS["text_primary"], border_width=0,
            corner_radius=SIZES["radius_sm"], wrap="word", state="disabled",
        )
        self.results_text.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        _ContextMenu(self.results_text)

    # ══════════════════════════════════════════════════════
    #  DOCUMENT MANAGEMENT
    # ══════════════════════════════════════════════════════

    def _add_documents(self):
        paths = filedialog.askopenfilenames(title="Выберите документы",
                                            filetypes=SUPPORTED_DOC_FORMATS)
        if paths:
            self._process_documents(list(paths), welcome=False)

    def _process_documents(self, file_paths: list[str], welcome: bool = False):
        """Process and index documents. Shows files instantly with spinner."""

        if self.rag_engine is None:
            key = self.api_key
            if not key:
                try:
                    key = self.api_entry.get().strip()
                except Exception:
                    pass
            if not key:
                messagebox.showwarning("Ошибка", "Сначала введите Gemini API-ключ.")
                return
            self.api_key = key
            try:
                self.yc_folder_id = self.yc_folder_entry.get().strip()
                self.yc_api_key = self.yc_key_entry.get().strip()
            except Exception:
                pass
            if not self.yc_folder_id or not self.yc_api_key:
                messagebox.showwarning("Ошибка", "Введите Yandex Cloud Folder ID и API Key.")
                return
            self._init_rag()
            if self.rag_engine is None:
                return

        # Immediately add all files to the list with "loading" state
        pending_cards = {}
        for fp in file_paths:
            name = Path(fp).name
            card = self._add_doc_card_loading(name, welcome=welcome)
            pending_cards[fp] = (name, card)

        def _status(msg, color=COLORS["text_secondary"]):
            def _update():
                if welcome:
                    self.welcome_status.configure(text=msg, text_color=color)
                else:
                    self.ws_status_lbl.configure(text=msg, text_color=color)
                    self.sidebar_load_lbl.configure(text=msg, text_color=color)
            self.after(0, _update)

        def worker():
            total = len(file_paths)
            ok_count = 0
            errors: list[str] = []

            for i, fp in enumerate(file_paths, 1):
                name, card = pending_cards[fp]
                _status(f"Загрузка {i}/{total}: {name}…", COLORS["orange"])
                try:
                    source_name = self.rag_engine.add_file(fp)
                    if source_name not in self.loaded_docs:
                        self.loaded_docs.append(source_name)
                    self.after(0, lambda c=card, n=source_name:
                               self._finish_doc_card(c, n, success=True))
                    ok_count += 1
                except Exception as e:
                    err = str(e)
                    errors.append(f"{name}: {err[:80]}")
                    self.after(0, lambda c=card, n=name, er=err:
                               self._finish_doc_card(c, n, success=False, error=er[:60]))

            self.after(0, self._update_doc_count)

            if ok_count > 0 and not errors:
                _status(f"✓ Загружено: {ok_count} док.", COLORS["green"])
            elif ok_count > 0 and errors:
                _status(f"✓ {ok_count} загружено, {len(errors)} ошибок", COLORS["orange"])
            else:
                detail = errors[0] if errors else "Неизвестная ошибка"
                _status(f"✗ Ошибка: {detail[:80]}", COLORS["red"])

            _finish_progress()

        def _finish_progress():
            def _do():
                if welcome:
                    pass  # no progress bar to stop in new design
                else:
                    try:
                        self.sidebar_progress.stop()
                        self.sidebar_progress.pack_forget()
                        self.add_docs_btn.configure(state="normal")
                        self.after(5000, lambda: self.sidebar_load_fr.pack_forget())
                    except Exception:
                        pass
            self.after(0, _do)

        if not welcome:
            self.sidebar_load_fr.pack(fill="x", padx=16, pady=(4, 0))
            self.sidebar_load_lbl.configure(text="Загрузка…", text_color=COLORS["orange"])
            self.sidebar_progress.pack(fill="x", pady=(4, 0))
            self.sidebar_progress.start()
            self.add_docs_btn.configure(state="disabled")

        threading.Thread(target=worker, daemon=True).start()

    def _add_doc_card_loading(self, name: str, welcome: bool = False) -> ctk.CTkFrame:
        """Add a doc card in 'loading' state — spinner + name, no delete yet."""
        parent = self.welcome_doc_list if welcome else self.doc_list_fr

        # Hide empty label
        if welcome:
            self.welcome_empty_lbl.pack_forget()
        else:
            self.empty_docs_lbl.pack_forget()

        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_tertiary"],
                            corner_radius=SIZES["radius_sm"], height=42)
        card.pack(fill="x", padx=2, pady=2)
        card.pack_propagate(False)

        # Spinner dots animation
        spinner_lbl = ctk.CTkLabel(card, text="⏳", font=FONTS["small"],
                                   text_color=COLORS["orange"], width=24)
        spinner_lbl.pack(side="left", padx=(10, 4))

        display_name = name if len(name) <= 30 else name[:27] + "…"
        ctk.CTkLabel(card, text=display_name, font=FONTS["body_small"],
                     text_color=COLORS["text_secondary"], anchor="w"
                     ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        # Animate spinner
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        card._anim_idx = 0
        card._anim_running = True

        def animate():
            if not card._anim_running or not card.winfo_exists():
                return
            card._anim_idx = (card._anim_idx + 1) % len(frames)
            try:
                spinner_lbl.configure(text=frames[card._anim_idx])
            except Exception:
                return
            card.after(100, animate)

        card.after(100, animate)
        card._spinner = spinner_lbl
        return card

    def _finish_doc_card(self, card: ctk.CTkFrame, name: str,
                         success: bool, error: str = ""):
        """Transition a loading card to success (with delete) or error state."""
        if not card.winfo_exists():
            return
        card._anim_running = False

        # Remove spinner
        try:
            card._spinner.destroy()
        except Exception:
            pass

        if success:
            # Clear old children and rebuild
            for w in card.winfo_children():
                w.destroy()

            display_name = name if len(name) <= 28 else name[:25] + "…"
            ctk.CTkLabel(card, text="✓", font=FONTS["small"],
                         text_color=COLORS["green"], width=20
                         ).pack(side="left", padx=(10, 2))
            ctk.CTkLabel(card, text=display_name, font=FONTS["body_small"],
                         text_color=COLORS["text_primary"], anchor="w"
                         ).pack(side="left", fill="x", expand=True, padx=(0, 4))
            ctk.CTkButton(
                card, text="✕", width=26, height=26, font=FONTS["small"],
                fg_color="transparent", hover_color=COLORS["btn_danger"],
                text_color=COLORS["text_tertiary"], corner_radius=6,
                command=lambda: self._remove_doc(name, card),
            ).pack(side="right", padx=6)

            self._doc_cards[name] = card
        else:
            for w in card.winfo_children():
                w.destroy()
            ctk.CTkLabel(card, text="✗", font=FONTS["small"],
                         text_color=COLORS["red"], width=20
                         ).pack(side="left", padx=(10, 2))
            err_txt = f"{name}: {error}" if error else name
            if len(err_txt) > 35:
                err_txt = err_txt[:32] + "…"
            ctk.CTkLabel(card, text=err_txt, font=FONTS["body_small"],
                         text_color=COLORS["red"], anchor="w"
                         ).pack(side="left", fill="x", expand=True, padx=(0, 4))
            ctk.CTkButton(
                card, text="✕", width=26, height=26, font=FONTS["small"],
                fg_color="transparent", hover_color=COLORS["btn_secondary_hover"],
                text_color=COLORS["text_tertiary"], corner_radius=6,
                command=lambda: card.destroy(),
            ).pack(side="right", padx=6)

    def _add_doc_card(self, name: str):
        """Add a fully-loaded doc card (used when restoring existing docs)."""
        self.empty_docs_lbl.pack_forget()
        try:
            self.welcome_empty_lbl.pack_forget()
        except Exception:
            pass

        card = ctk.CTkFrame(self.doc_list_fr, fg_color=COLORS["bg_tertiary"],
                            corner_radius=SIZES["radius_sm"], height=42)
        card.pack(fill="x", padx=2, pady=2)
        card.pack_propagate(False)

        display_name = name if len(name) <= 28 else name[:25] + "…"
        ctk.CTkLabel(card, text="✓", font=FONTS["small"],
                     text_color=COLORS["green"], width=20
                     ).pack(side="left", padx=(10, 2))
        ctk.CTkLabel(card, text=display_name, font=FONTS["body_small"],
                     text_color=COLORS["text_primary"], anchor="w"
                     ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            card, text="✕", width=26, height=26, font=FONTS["small"],
            fg_color="transparent", hover_color=COLORS["btn_danger"],
            text_color=COLORS["text_tertiary"], corner_radius=6,
            command=lambda: self._remove_doc(name, card),
        ).pack(side="right", padx=6)

        self._doc_cards[name] = card

        # Also add to welcome list
        try:
            w_card = ctk.CTkFrame(self.welcome_doc_list, fg_color=COLORS["bg_tertiary"],
                                  corner_radius=SIZES["radius_sm"], height=36)
            w_card.pack(fill="x", padx=2, pady=2)
            w_card.pack_propagate(False)
            ctk.CTkLabel(w_card, text=f"✓  {display_name}", font=FONTS["small"],
                         text_color=COLORS["green"], anchor="w"
                         ).pack(side="left", padx=10, fill="x", expand=True)
        except Exception:
            pass

    def _remove_doc(self, name, card):
        if self.rag_engine:
            try:
                self.rag_engine.remove_document(name)
            except Exception:
                pass
        if name in self.loaded_docs:
            self.loaded_docs.remove(name)
        self._doc_cards.pop(name, None)
        card.destroy()
        self._update_doc_count()
        if not self.loaded_docs:
            self.empty_docs_lbl.pack(pady=40)

    def _clear_all_docs(self):
        if not messagebox.askyesno("Подтверждение", "Удалить все документы?"):
            return
        if self.rag_engine:
            self.rag_engine.clear_all()
        self.loaded_docs.clear()
        self._doc_cards.clear()
        for w in self.doc_list_fr.winfo_children():
            if w is not self.empty_docs_lbl:
                w.destroy()
        self.empty_docs_lbl.pack(pady=40)
        # Also clear welcome list
        try:
            for w in self.welcome_doc_list.winfo_children():
                if w is not self.welcome_empty_lbl:
                    w.destroy()
            self.welcome_empty_lbl.pack(pady=30)
        except Exception:
            pass
        self._update_doc_count()

    def _load_existing_docs(self):
        if not self.rag_engine:
            return
        for name in self.rag_engine.get_loaded_sources():
            if name not in self.loaded_docs:
                self.loaded_docs.append(name)
                self._add_doc_card(name)
        self._update_doc_count()

    def _update_doc_count(self):
        count = str(len(self.loaded_docs))
        try:
            self.doc_count_lbl.configure(text=count)
        except Exception:
            pass
        try:
            self.welcome_doc_count.configure(text=count)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  IMAGE
    # ══════════════════════════════════════════════════════

    def _select_image(self):
        path = filedialog.askopenfilename(title="Выберите изображение",
                                          filetypes=SUPPORTED_IMAGE_FORMATS)
        if path:
            self.current_image_path = path
            self._show_preview(path)
            self.del_img_btn.configure(state="normal")
            self.img_name_lbl.configure(text=Path(path).name)

    def _paste_image(self, event=None):
        """Paste image from clipboard (screenshot, copied image, etc.)."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception:
            img = None

        if img is None:
            messagebox.showinfo("Буфер обмена",
                                "В буфере обмена нет изображения.\n"
                                "Скопируйте картинку или сделайте скриншот (Win+Shift+S).")
            return

        # Save to temp file so the analysis pipeline can use a file path
        tmp_dir = Path(tempfile.gettempdir()) / "ad_censor"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / f"clipboard_{id(img)}.png"
        try:
            img.save(str(tmp_path), format="PNG")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить изображение:\n{e}")
            return

        self.current_image_path = str(tmp_path)
        self._show_preview(str(tmp_path))
        self.del_img_btn.configure(state="normal")
        self.img_name_lbl.configure(text="📋 Из буфера обмена")

    def _remove_image(self):
        self.current_image_path = None
        self._img_ref = None
        self.img_label.configure(image=None,
                                 text="Нажмите «Выбрать»\nчтобы загрузить\nизображение")
        self.del_img_btn.configure(state="disabled")
        self.img_name_lbl.configure(text="")

    def _show_preview(self, path):
        try:
            img = Image.open(path)
            mw, mh = MAX_IMAGE_PREVIEW
            r = min(mw / img.width, mh / img.height, 1.0)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                   size=(int(img.width * r), int(img.height * r)))
            self._img_ref = ctk_img
            self.img_label.configure(image=ctk_img, text="")
        except Exception as e:
            self.img_label.configure(text=f"Ошибка: {e}")

    # ══════════════════════════════════════════════════════
    #  ANALYSIS
    # ══════════════════════════════════════════════════════

    def _run_analysis(self):
        if self._is_analyzing:
            return
        text = self.text_input.get("1.0", "end").strip()
        img = self.current_image_path
        if not text and not img:
            messagebox.showinfo("Нечего проверять", "Введите текст или прикрепите изображение.")
            return
        if not self.rag_engine:
            messagebox.showwarning("Ошибка", "RAG Engine не инициализирован.")
            return

        self._is_analyzing = True
        self._stop_event.clear()
        self.analyze_btn.configure(state="disabled", text="Анализирую…")
        self.stop_btn.configure(state="normal")
        self.analysis_progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.analysis_progress.start()

        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.insert("1.0", "Идёт анализ, подождите…")
        self.results_text.configure(state="disabled")
        self.result_status_lbl.configure(text="⏳ Обработка…", text_color=COLORS["orange"])
        self._risk_frame.grid_remove()

        def worker():
            try:
                if self._stop_event.is_set():
                    self.after(0, lambda: self._finish_analysis("Анализ отменён."))
                    return
                result = self.rag_engine.analyze(text=text, image_path=img)
                if self._stop_event.is_set():
                    self.after(0, lambda: self._finish_analysis("Анализ отменён."))
                    return
                self.after(0, lambda: self._finish_analysis(result))
            except Exception as e:
                err_msg = f"Ошибка при анализе:\n\n{e}"
                self.after(0, lambda m=err_msg: self._finish_analysis(m))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_analysis(self):
        self._stop_event.set()
        self._finish_analysis("Анализ остановлен пользователем.")

    def _clear_result(self):
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.configure(state="disabled")
        self.result_status_lbl.configure(text="")
        self._risk_frame.grid_remove()
        self.risk_pct_lbl.configure(text="—", text_color=COLORS["text_tertiary"])
        self.risk_bar.set(0)
        self.risk_bar.configure(progress_color=COLORS["green"])
        self.risk_label.configure(text="")

    def _finish_analysis(self, text: str):
        self._is_analyzing = False
        self.analyze_btn.configure(state="normal", text="Проверить материал")
        self.stop_btn.configure(state="disabled")
        try:
            self.analysis_progress.stop()
            self.analysis_progress.grid_forget()
        except Exception:
            pass

        risk_pct = self._extract_risk(text)
        self._update_risk_indicator(risk_pct, text)

        if "отменён" in text or "остановлен" in text:
            self.result_status_lbl.configure(text="Отменено", text_color=COLORS["text_tertiary"])
        elif "Ошибка" in text:
            self.result_status_lbl.configure(text="Ошибка", text_color=COLORS["red"])
        elif "ЗАПРЕЩЕНО" in text:
            self.result_status_lbl.configure(text="🔴 Запрещено", text_color=COLORS["red"])
        elif "ТРЕБУЕТ ДОРАБОТКИ" in text:
            self.result_status_lbl.configure(text="🟡 Доработать", text_color=COLORS["orange"])
        elif "ДОПУСТИМО" in text:
            self.result_status_lbl.configure(text="🟢 Допустимо", text_color=COLORS["green"])
        else:
            self.result_status_lbl.configure(text="Готово", text_color=COLORS["green"])

        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.insert("1.0", text)
        self.results_text.configure(state="disabled")

    def _extract_risk(self, text: str) -> Optional[int]:
        m = re.search(r"(?:РИСК|RISK)[:\s]*(\d{1,3})\s*%", text, re.IGNORECASE)
        if m:
            return min(int(m.group(1)), 100)
        return None

    def _update_risk_indicator(self, pct: Optional[int], text: str):
        if pct is None:
            self._risk_frame.grid_remove()
            return

        self._risk_frame.grid()

        if pct <= 25:
            color = COLORS["green"]
            label = "Низкий риск"
        elif pct <= 50:
            color = "#4ade80"
            label = "Умеренный"
        elif pct <= 75:
            color = COLORS["orange"]
            label = "Высокий риск"
        else:
            color = COLORS["red"]
            label = "Критический!"

        self.risk_pct_lbl.configure(text=f"{pct}%", text_color=color)
        self.risk_bar.configure(progress_color=color)
        self.risk_bar.set(pct / 100)
        self.risk_label.configure(text=label, text_color=color)
