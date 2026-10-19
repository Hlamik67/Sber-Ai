"""Нарезка документа на куски (чанки) с перекрытием."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

# Импортируем Document для типизации.
# (Если хотите полностью убрать зависимость от ingest, можно использовать typing.Any)
from ingest import Document

DEFAULT_CHUNK_SIZE = 1000  # символов
DEFAULT_OVERLAP = 150      # символов


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    file_name: str
    text: str
    section_path: list[str]
    page_number: Optional[int]
    start_char: int
    end_char: int


def chunk_document(
    doc: Document,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Разбить документ на куски, уважая границы страниц и разделов."""
    if overlap >= chunk_size:
        raise ValueError("overlap должен быть меньше chunk_size")

    chunks: list[Chunk] = []
    global_char_offset = 0

    if doc.pages:
        # --- Ветка для PDF (есть страницы) ---
        valid_pages = []
        for page in doc.pages:
            clean_text = _clean_toc(page.text)
            if len(clean_text.strip()) > 50:
                valid_pages.append((page, clean_text))

        for page, page_text in valid_pages:
            page_chunks = _split_text(
                text=page_text,
                chunk_size=chunk_size,
                overlap=overlap,
                doc=doc,
                section_path=page.section_path,
                page_number=page.page_number,
                start_offset=global_char_offset
            )
            chunks.extend(page_chunks)
            global_char_offset += len(page_text) + 2
    else:
        # --- Ветка для MD и DOCX (нет страниц) ---
        clean_text = _clean_toc(doc.text)

        # 1. Нарезаем без разделов
        page_chunks = _split_text(
            text=clean_text,
            chunk_size=chunk_size,
            overlap=overlap,
            doc=doc,
            section_path=[],
            page_number=None,
            start_offset=0
        )

        # 2. Назначаем разделы на основе позиций заголовков из ingest
        if doc.sections:
            _assign_sections(page_chunks, clean_text, doc.sections)

        chunks.extend(page_chunks)

    return chunks


def _assign_sections(chunks: list[Chunk], text: str, sections: list) -> None:
    """Проставить section_path кускам на основе позиций заголовков в тексте."""
    if not sections:
        return

    # Разбиваем текст на строки с их позициями (для поиска заголовков)
    lines = text.splitlines(keepends=True)
    line_positions = []
    current_pos = 0
    for line in lines:
        line_positions.append((current_pos, line.strip()))
        current_pos += len(line)

    positioned_sections = []

    # Ищем каждый заголовок среди строк текста
    for sec in sections:
        found = False
        for pos, line_text in line_positions:
            if line_text == sec.title.strip():
                positioned_sections.append((pos, sec))
                found = True
                break
        if not found:
            # Запасной вариант: поиск подстроки
            pos = text.find(sec.title)
            if pos != -1:
                positioned_sections.append((pos, sec))

    if not positioned_sections:
        return

    # Сортируем заголовки по их позиции в тексте
    positioned_sections.sort(key=lambda x: x[0])

    current_stack = []
    sec_idx = 0

    for chunk in chunks:
        # Обновляем стек разделов, которые начались до начала этого куска
        while sec_idx < len(positioned_sections) and positioned_sections[sec_idx][0] <= chunk.start_char:
            pos, sec = positioned_sections[sec_idx]
            current_stack = current_stack[:sec.level - 1] + [sec.title]
            sec_idx += 1

        chunk.section_path = list(current_stack)


def _clean_toc(text: str) -> str:
    """Удаляет строки, которые выглядят как оглавление."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if re.search(r"\.{4,}", line):
            continue
        if re.match(r"^[\s\.\d]+$", line) and len(line) > 10:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _split_text(
    text: str,
    chunk_size: int,
    overlap: int,
    doc: Document,
    section_path: list[str],
    page_number: Optional[int],
    start_offset: int
) -> list[Chunk]:
    """Внутренняя функция для нарезки сплошного текста."""
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size

        # Ищем красивый конец для текущего куска (точка или перенос строки)
        if end < text_len:
            lookback_start = max(start, end - 300)
            chunk_str = text[lookback_start:end]

            cut_idx = max(
                chunk_str.rfind('. '),
                chunk_str.rfind('.\n'),
                chunk_str.rfind('\n\n')
            )

            if cut_idx != -1:
                end = lookback_start + cut_idx + 1
            else:
                space_idx = chunk_str.rfind(' ')
                if space_idx != -1:
                    end = lookback_start + space_idx

        chunk_text = text[start:end].strip()

        if chunk_text and len(chunk_text) > 100:
            chunk_id = _generate_chunk_id(doc.file_hash, chunk_text, start_offset + start)

            chunks.append(Chunk(
                chunk_id=chunk_id,
                document_id=doc.file_hash,
                file_name=doc.file_name,
                text=chunk_text,
                section_path=section_path,
                page_number=page_number,
                start_char=start_offset + start,
                end_char=start_offset + end
            ))

        # Сдвигаем старт для следующего куска с учетом перекрытия
        next_start = end - overlap

        # Чтобы не обрезать слово в начале нового куска, сдвигаем старт до пробела
        if next_start > 0:
            space_idx = text.find(' ', next_start)
            if space_idx != -1 and space_idx < end:
                next_start = space_idx + 1

        start = next_start
        if start >= end:
            break

    return chunks


def _generate_chunk_id(file_hash: str, text: str, position: int) -> str:
    """Сгенерировать стабильный ID на основе хэша файла и содержимого."""
    raw_id = f"{file_hash}:{position}:{text}"
    return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]