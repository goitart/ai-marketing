"""
Тема оформления — Yandex Translate dark style.

Глубокий тёмный фон, тёплый красно-коралловый акцент, крупные карточки
с большими радиусами скругления. Крупный текст, просторная компоновка.
"""

# ── Цветовая палитра (Yandex Dark) ──────────────────────

COLORS = {
    # Фоны — глубокий чёрный с тёплым оттенком
    "bg_primary": "#141414",
    "bg_secondary": "#1e1e1e",
    "bg_tertiary": "#2a2a2a",
    "bg_input": "#1a1a1a",
    "bg_elevated": "#242424",
    "bg_info": "#1c2333",

    # Акценты (Yandex Red / Coral)
    "accent": "#ff4438",
    "accent_hover": "#ff6b5e",
    "accent_muted": "#3d1c1a",

    # Текст
    "text_primary": "#f0f0f0",
    "text_secondary": "#8a8a8a",
    "text_tertiary": "#555555",
    "text_link": "#7cacf8",

    # Границы — очень тонкие, почти невидимые
    "border": "#2a2a2a",
    "border_light": "#363636",

    # Семантика
    "green": "#4ade80",
    "orange": "#fb923c",
    "red": "#ff4438",
    "indigo": "#818cf8",

    # Кнопки
    "btn_primary": "#ff4438",
    "btn_primary_hover": "#ff6b5e",
    "btn_secondary": "#2a2a2a",
    "btn_secondary_hover": "#363636",
    "btn_danger": "#ff4438",
    "btn_danger_hover": "#ff6b5e",
}

# ── Шрифты (Segoe UI = современный Windows шрифт) ───────

FONTS = {
    "hero": ("Segoe UI", 34, "bold"),
    "title": ("Segoe UI", 22, "bold"),
    "heading": ("Segoe UI", 16, "bold"),
    "subheading": ("Segoe UI", 15, "bold"),
    "body": ("Segoe UI", 14),
    "body_medium": ("Segoe UI", 14, "bold"),
    "body_small": ("Segoe UI", 13),
    "caption": ("Segoe UI", 12),
    "caption_bold": ("Segoe UI", 12, "bold"),
    "small": ("Segoe UI", 11),
    "small_bold": ("Segoe UI", 11, "bold"),
    "mono": ("Cascadia Code", 13),
}

# ── Размеры ─────────────────────────────────────────────

SIZES = {
    "sidebar_w": 280,
    "radius_xl": 24,
    "radius_lg": 20,
    "radius_md": 14,
    "radius_sm": 10,
    "pad_xl": 28,
    "pad_lg": 20,
    "pad_md": 14,
    "pad_sm": 8,
    "btn_h": 42,
    "btn_h_lg": 50,
    "entry_h": 46,
}
