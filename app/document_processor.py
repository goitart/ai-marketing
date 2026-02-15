"""
Модуль обработки документов.

Поддерживаемые форматы: PDF, DOCX, TXT.
Тексты разбиваются на чанки с перекрытием для RAG‑индексации.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List


class DocumentProcessor:
    """Загрузка документов и разбиение на чанки."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ── Парсинг разных форматов ──────────────────────────

    def load_document(self, file_path: str) -> str:
        """Прочитать содержимое документа и вернуть текст."""
        ext = Path(file_path).suffix.lower()
        loaders = {
            ".pdf": self._load_pdf,
            ".docx": self._load_docx,
            ".txt": self._load_txt,
        }
        loader = loaders.get(ext)
        if loader is None:
            raise ValueError(f"Неподдерживаемый формат файла: {ext}")
        return loader(file_path)

    @staticmethod
    def _load_pdf(path: str) -> str:
        from PyPDF2 import PdfReader

        reader = PdfReader(path)
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages)

    @staticmethod
    def _load_docx(path: str) -> str:
        from docx import Document

        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    @staticmethod
    def _load_txt(path: str) -> str:
        encodings = ["utf-8", "cp1251", "latin-1"]
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        raise ValueError(f"Не удалось прочитать файл: {path}")

    # ── Разбиение на чанки ───────────────────────────────

    def split_into_chunks(self, text: str, source: str = "") -> List[Dict]:
        """Разбить текст на чанки с перекрытием."""
        text = text.strip()
        if not text:
            return []

        chunks: list[dict] = []
        start = 0
        chunk_id = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]

            # Попробовать разбить по границе предложения
            if end < len(text):
                for sep in (".\n", "\n", ". ", "? ", "! "):
                    pos = chunk_text.rfind(sep)
                    if pos > self.chunk_size * 0.4:
                        chunk_text = chunk_text[: pos + len(sep)]
                        end = start + len(chunk_text)
                        break

            clean = chunk_text.strip()
            if clean:
                chunks.append(
                    {
                        "id": f"{source}_chunk_{chunk_id}",
                        "text": clean,
                        "source": source,
                        "chunk_index": chunk_id,
                    }
                )
                chunk_id += 1

            start = end - self.chunk_overlap
            if start >= len(text):
                break
            # Предохранитель от бесконечного цикла
            if end == len(text):
                break

        return chunks

    # ── Полный pipeline ──────────────────────────────────

    def process_document(self, file_path: str) -> List[Dict]:
        """Загрузить документ → разбить на чанки."""
        text = self.load_document(file_path)
        source = Path(file_path).name
        return self.split_into_chunks(text, source)
