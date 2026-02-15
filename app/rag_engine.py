"""
RAG-движок: Yandex AI Studio (полный RAG) + Gemini (только описание изображений).

Архитектура:
  - Документы загружаются в Yandex Cloud AI Studio Search Index
  - YandexGPT-ассистент выполняет полный анализ рекламы с контекстом из базы знаний
  - Gemini используется ТОЛЬКО для описания содержимого изображений,
    которое затем передаётся YandexGPT для юридического анализа
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
from yandex_ai_studio_sdk import AIStudio

from app.config import (
    GEMINI_MODEL,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
)


def _retry_call(fn, max_retries=MAX_RETRIES, base_delay=RETRY_BASE_DELAY):
    """Call fn() with exponential backoff on 429 / ResourceExhausted errors."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "ResourceExhausted" in err_str
            if is_rate_limit and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
                last_err = e
                continue
            raise
    raise last_err  # type: ignore[misc]


# -- Промпты ------------------------------------------------------

ASSISTANT_INSTRUCTION = """Вы — экспертная система проверки рекламных материалов на соответствие
российскому законодательству. Вы досконально знаете:

* ФЗ No38-ФЗ "О рекламе" (все статьи)
* Требования к маркировке интернет-рекламы (Закон о маркировке, ОРД, токен erid)
* ФЗ "О защите прав потребителей"
* Ст. 14.3 КоАП РФ (ответственность за ненадлежащую рекламу)
* Ограничения по рекламе алкоголя (ст. 21), табака (ст. 23), лекарств (ст. 24),
  финансовых услуг (ст. 28), БАДов, азартных игр, оружия.
* Требования к рекламе для детей (ст. 6), скрытой рекламе (ст. 5 п. 9),
  недостоверной и недобросовестной рекламе (ст. 5).
* Требования к дисклеймерам и предупреждениям.

У вас есть доступ к базе знаний с нормативными документами пользователя.
ОБЯЗАТЕЛЬНО используйте найденные документы из базы знаний для обоснования
своих выводов. Цитируйте конкретные статьи и пункты.

ЗАДАЧА: проанализируйте предоставленный рекламный материал.

ВАЖНО: При анализе ОБЯЗАТЕЛЬНО ссылайтесь на конкретные фрагменты из базы знаний.
Если найдены релевантные нормы - укажите: "Согласно [название документа], статья X, пункт Y: ...".

Верните ответ СТРОГО в следующем формате:

======================================
УРОВЕНЬ РИСКА: [X]%
СТАТУС: [ДОПУСТИМО | ТРЕБУЕТ ДОРАБОТКИ | ЗАПРЕЩЕНО]
======================================

ИСПОЛЬЗОВАННЫЕ ИСТОЧНИКИ:
[Перечислите документы из базы знаний, на которые вы опирались]

ВЫЯВЛЕННЫЕ НАРУШЕНИЯ:
[Пронумерованный список нарушений. Для каждого указать:
 - описание проблемы
 - ссылку на конкретную статью/пункт закона
 - цитату из документа
 - почему это нарушение]

РЕКОМЕНДАЦИИ ПО ИСПРАВЛЕНИЮ:
[Конкретные действия для устранения каждого нарушения]

ДОПОЛНИТЕЛЬНЫЕ ЗАМЕЧАНИЯ:
[Общие советы по улучшению материала]
======================================

Если нарушений нет - укажите Риск 0-10 % и статус ДОПУСТИМО с пояснением.
Отвечайте на русском языке. Будьте конкретны и ссылайтесь на статьи закона."""


IMAGE_DESCRIBE_PROMPT = """Подробно опиши содержимое этого рекламного материала (изображения).
Укажи:
1. Что изображено (товар, услуга, люди, символы, логотипы)
2. Весь текст, который есть на изображении (дословно)
3. Мелкий текст / дисклеймеры (если есть)
4. Визуальные приёмы (яркие цвета, привлечение внимания, люди, дети)
5. Целевая аудитория (предположительно)
6. Есть ли маркировка "Реклама" / токен erid / указание рекламодателя

Отвечай на русском языке. Будь максимально подробен и точен.
Не делай юридических выводов - только опиши содержание."""


# -- Yandex gRPC error helper -------------------------------------

def _parse_grpc_error(e: Exception) -> str:
    """Extract a human-readable message from gRPC / Yandex SDK errors."""
    msg = str(e)
    status_hints = {
        "PERMISSION_DENIED": (
            "Доступ запрещён. Проверьте:\n"
            "  1) API-ключ корректный (не OAuth-токен)\n"
            "  2) API-ключ привязан к сервисному аккаунту с ролями:\n"
            "     - ai.editor (или ai.admin)\n"
            "     - ai.assistants.editor\n"
            "  3) Folder ID - правильный\n"
            "  4) Сервисный аккаунт состоит в этом каталоге"
        ),
        "UNAUTHENTICATED": (
            "Не авторизован. API-ключ недействителен или истёк.\n"
            "Создайте новый ключ на https://console.yandex.cloud/"
        ),
        "NOT_FOUND": "Ресурс не найден. Проверьте Folder ID.",
        "INVALID_ARGUMENT": "Неверный аргумент. Файл повреждён или формат не поддерживается.",
        "RESOURCE_EXHAUSTED": "Лимит исчерпан. Подождите или увеличьте квоту.",
    }
    for code, hint in status_hints.items():
        if code in msg:
            return f"{hint}\n\nОригинал: {msg[:200]}"
    return msg[:300]


# -- YandexRAG -----------------------------------------------------

class YandexRAG:
    """Full RAG via Yandex AI Studio: files + search index + YandexGPT assistant."""

    def __init__(self, folder_id: str, api_key: str):
        self.sdk = AIStudio(folder_id=folder_id, auth=api_key)
        self._search_index = None
        self._files: dict[str, str] = {}
        self._assistant = None
        self._index_dirty = True
        self._load_existing_index()

    def test_connection(self) -> str:
        import re
        try:
            list(self.sdk.files.list())
            files_ok, files_err = True, ""
        except Exception as e:
            files_ok, files_err = False, str(e)
        try:
            list(self.sdk.search_indexes.list())
            idx_ok, idx_err = True, ""
        except Exception as e:
            idx_ok, idx_err = False, str(e)
        if files_ok and idx_ok:
            return "ok"
        lines = ["=== Диагностика Yandex Cloud ===", ""]
        for label, ok, err in [("Файлы (files.list)", files_ok, files_err),
                                ("Индексы (search_indexes.list)", idx_ok, idx_err)]:
            status = "OK" if ok else "FAIL"
            lines.append(f"[{status}] {label}")
            if not ok:
                detail = re.search(r'detail\s*=\s*"([^"]*)"', err)
                lines.append(f"    -> {detail.group(1) if detail else err[:150]}")
        lines.append("")
        all_errs = files_err + idx_err
        if "PERMISSION_DENIED" in all_errs:
            lines += ["Роли назначены сервисному аккаунту?",
                       "Нужные роли:", "  - ai.assistants.editor", "  - ai.editor (или ai.admin)"]
        elif "UNAUTHENTICATED" in all_errs:
            lines.append("API-ключ недействителен.")
        lines += ["", "=== Raw ===", (files_err or idx_err)[:300]]
        return "\n".join(lines)

    def _load_existing_index(self):
        """Try to find existing search index and restore file names."""
        try:
            for si in self.sdk.search_indexes.list():
                if si.labels and si.labels.get("app") == "ad_censor":
                    self._search_index = si
                    for idx_file in si.list_files():
                        fid = idx_file.id
                        # Restore original filename from Yandex file metadata
                        fname = fid
                        try:
                            full_file = self.sdk.files.get(fid)
                            fname = getattr(full_file, "name", None) or fid
                        except Exception:
                            pass
                        self._files[fname] = fid
                    self._index_dirty = True
                    break
        except Exception:
            pass

    def _ensure_assistant(self):
        """Create or recreate the YandexGPT assistant with search index tool."""
        if self._assistant is not None and not self._index_dirty:
            return
        if self._assistant is not None:
            try:
                self._assistant.delete()
            except Exception:
                pass
            self._assistant = None
        tools = []
        if self._search_index is not None:
            tools.append(self.sdk.tools.search_index(self._search_index))
        self._assistant = self.sdk.assistants.create(
            "yandexgpt", tools=tools, instruction=ASSISTANT_INSTRUCTION,
        )
        self._index_dirty = False

    def add_file(self, file_path: str) -> str:
        """Upload a file to Yandex and add to search index. Returns source name."""
        name = Path(file_path).name
        try:
            yc_file = self.sdk.files.upload(
                file_path, name=name, ttl_days=365, expiration_policy="since_last_active",
            )
        except Exception as e:
            raise RuntimeError(
                f"Ошибка загрузки файла '{name}':\n{_parse_grpc_error(e)}"
            ) from e
        self._files[name] = yc_file.id
        try:
            if self._search_index is None:
                op = self.sdk.search_indexes.create_deferred(
                    yc_file, name="ad_censor_index", labels={"app": "ad_censor"},
                )
                self._search_index = op.wait(poll_interval=1.0)
            else:
                op = self._search_index.add_files_deferred(yc_file)
                op.wait(poll_interval=1.0)
        except Exception as e:
            raise RuntimeError(
                f"Файл '{name}' загружен, но ошибка индексации:\n{_parse_grpc_error(e)}"
            ) from e
        self._index_dirty = True
        return name

    def remove_file(self, source_name: str):
        fid = self._files.pop(source_name, None)
        if fid:
            try:
                self.sdk.files.get(fid).delete()
            except Exception:
                pass

    def clear_all(self):
        if self._assistant:
            try:
                self._assistant.delete()
            except Exception:
                pass
            self._assistant = None
        if self._search_index:
            try:
                self._search_index.delete()
            except Exception:
                pass
            self._search_index = None
        for fid in list(self._files.values()):
            try:
                self.sdk.files.get(fid).delete()
            except Exception:
                pass
        self._files.clear()
        self._index_dirty = True

    def get_loaded_sources(self) -> list[str]:
        return sorted(self._files.keys())

    def document_count(self) -> int:
        return len(self._files)

    def analyze(self, query: str) -> str:
        """Full RAG analysis via YandexGPT assistant with search index."""
        self._ensure_assistant()
        if self._assistant is None:
            return "(Ошибка: не удалось создать ассистента YandexGPT)"
        try:
            thread = self.sdk.threads.create()
            thread.write(query)
            run = self._assistant.run(thread)
            result = run.wait(poll_interval=0.5)
            text = result.text if result.text else ""
            try:
                thread.delete()
            except Exception:
                pass
            if text.strip():
                return text
            return "(YandexGPT вернул пустой ответ.)"
        except Exception as e:
            return f"Ошибка анализа через YandexGPT:\n{_parse_grpc_error(e)}"


# -- RAGEngine (main public API) -----------------------------------

class RAGEngine:
    """Main engine: Gemini for image description, Yandex for full RAG analysis."""

    def __init__(self, api_key: str, model_name: str = GEMINI_MODEL,
                 yc_folder_id: str = "", yc_api_key: str = ""):
        genai.configure(api_key=api_key)
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)
        self.yandex: Optional[YandexRAG] = None
        if yc_folder_id and yc_api_key:
            try:
                self.yandex = YandexRAG(folder_id=yc_folder_id, api_key=yc_api_key)
            except Exception as e:
                print(f"[WARN] Yandex RAG init failed: {e}")

    # -- Document management (delegated to Yandex) --

    def add_file(self, file_path: str) -> str:
        if not self.yandex:
            raise RuntimeError("Yandex Cloud не настроен. Укажите Folder ID и API Key.")
        return self.yandex.add_file(file_path)

    def remove_document(self, source_name: str) -> None:
        if self.yandex:
            self.yandex.remove_file(source_name)

    def clear_all(self) -> None:
        if self.yandex:
            self.yandex.clear_all()

    def get_loaded_sources(self) -> list[str]:
        return self.yandex.get_loaded_sources() if self.yandex else []

    def document_count(self) -> int:
        return self.yandex.document_count() if self.yandex else 0

    # -- Gemini: image description only --

    def set_model(self, model_name: str) -> None:
        """Switch Gemini model (used for image description only)."""
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)

    def describe_image(self, image_path: str) -> str:
        """Use Gemini to describe image contents (no legal analysis)."""
        from PIL import Image
        img = Image.open(image_path)
        max_dim = 2048
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        response = _retry_call(lambda: self.model.generate_content([IMAGE_DESCRIBE_PROMPT, img]))
        return response.text

    # -- Analysis (YandexGPT via assistant) --

    def analyze_text(self, text: str) -> str:
        """Send ad text to YandexGPT for full legal analysis."""
        if not self.yandex:
            return "Yandex Cloud не настроен. Анализ невозможен."
        query = (
            "Проверь следующий рекламный материал на соответствие "
            "российскому законодательству о рекламе:\n\n"
            f'"""\n{text}\n"""\n\n'
            f"[Документов в базе знаний: {self.document_count()}]"
        )
        return self.yandex.analyze(query)

    def analyze_image(self, image_path: str, text: str = "") -> str:
        """Describe image via Gemini, then send to YandexGPT for analysis."""
        if not self.yandex:
            return "Yandex Cloud не настроен. Анализ невозможен."
        try:
            description = self.describe_image(image_path)
        except Exception as e:
            return f"Ошибка описания изображения (Gemini):\n{str(e)[:200]}"
        query_parts = [
            "Проверь следующий рекламный материал на соответствие "
            "российскому законодательству о рекламе.", "",
            "ОПИСАНИЕ ВИЗУАЛЬНОГО СОДЕРЖАНИЯ:", '"""', description, '"""',
        ]
        if text.strip():
            query_parts += ["", "ТЕКСТ РЕКЛАМЫ:", '"""', text, '"""']
        query_parts.append(f"\n[Документов в базе знаний: {self.document_count()}]")
        return self.yandex.analyze("\n".join(query_parts))

    def analyze(self, text: str = "", image_path: Optional[str] = None) -> str:
        """Universal analysis: text, image, or both."""
        if image_path and os.path.isfile(image_path):
            return self.analyze_image(image_path, text)
        elif text.strip():
            return self.analyze_text(text)
        else:
            return "Не предоставлен материал для проверки. Введите текст или прикрепите изображение."
