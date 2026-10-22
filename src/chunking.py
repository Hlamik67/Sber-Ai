"""Нарезка документа на куски (чанки)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from ingest import Document
from config import CHUNK_SIZE

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
    chunk_size: int = CHUNK_SIZE,
) -> list[Chunk]:
    """Разбить документ на куски, уважая границы страниц и разделов.
    Перекрытие (overlap) убрано по требованию.
    """
    chunks: list[Chunk] = []
    global_char_offset = 0

    if doc.pages:
        # --- Ветка для PDF (постранично, без перекрытия) ---
        valid_pages = []
        for page in doc.pages:
            clean_text = _clean_text(page.text)
            if len(clean_text.strip()) > 50:
                valid_pages.append((page, clean_text))

        for page, page_text in valid_pages:
            page_chunks = _split_by_chars(
                text=page_text,
                chunk_size=chunk_size,
                doc=doc,
                section_path=page.section_path,
                page_number=page.page_number,
                start_offset=global_char_offset
            )
            chunks.extend(page_chunks)
            global_char_offset += len(page_text) + 2
    else:
        # --- Ветка для MD и DOCX (режем по заголовкам!) ---
        clean_text = _clean_text(doc.text)
        chunks = _split_by_sections(clean_text, doc, chunk_size)

    return chunks


def _split_by_sections(text: str, doc: Document, chunk_size: int) -> list[Chunk]:
    """Режет текст сначала по заголовкам, затем длинные секции по размеру."""
    chunks = []

    # 1. Находим все заголовки и их позиции в тексте
    header_positions = []

    if doc.file_type == "md":
        current_pos = 0
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped[level:].strip()
                if title:
                    header_positions.append((current_pos, level, title))
            current_pos += len(line)
    else:
        # Для DOCX ищем по списку секций из ingest.py
        for sec in doc.sections:
            pos = text.find(sec.title)
            if pos != -1:
                header_positions.append((pos, sec.level, sec.title))
        header_positions.sort(key=lambda x: x[0])

    # 2. Если заголовков нет, режем весь текст просто по размеру
    if not header_positions:
        return _split_by_chars(text, chunk_size, doc, [], None, 0)

    # 3. Формируем секции: каждый заголовок + текст до следующего заголовка
    current_stack = []
    sections_data = []

    for i, (pos, level, title) in enumerate(header_positions):
        current_stack = current_stack[:level - 1] + [title]

        start = pos
        # Конец секции — начало следующего заголовка или конец текста
        end = header_positions[i+1][0] if i + 1 < len(header_positions) else len(text)

        section_text = text[start:end].strip()
        if section_text:
            sections_data.append({
                "text": section_text,
                "section_path": list(current_stack),
                "start_offset": start
            })

    # 4. Нарезаем каждую секцию
    for sec in sections_data:
        # Если секция короткая — оставляем как есть (даже если меньше chunk_size)
        if len(sec["text"]) <= chunk_size:
            chunk_id = _generate_chunk_id(doc.file_hash, sec["text"], sec["start_offset"])
            chunks.append(Chunk(
                chunk_id=chunk_id,
                document_id=doc.file_hash,
                file_name=doc.file_name,
                text=sec["text"],
                section_path=sec["section_path"],
                page_number=None,
                start_char=sec["start_offset"],
                end_char=sec["start_offset"] + len(sec["text"])
            ))
        else:
            # Если секция длинная — режем внутри неё, но без перекрытия (overlap)
            sub_chunks = _split_by_chars(
                text=sec["text"],
                chunk_size=chunk_size,
                doc=doc,
                section_path=sec["section_path"],
                page_number=None,
                start_offset=sec["start_offset"]
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_by_chars(
    text: str,
    chunk_size: int,
    doc: Document,
    section_path: list[str],
    page_number: Optional[int],
    start_offset: int
) -> list[Chunk]:
    """Режет сплошной текст по размеру БЕЗ перекрытия."""
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size

        # Ищем красивое место для разреза (точка или пробел)
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

        # Если кусок не пустой (порог снизили до 10, чтобы заголовки не выкидывались)
        if chunk_text and len(chunk_text) > 10:
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

        # Сдвигаем старт вперед без перекрытия
        start = end

        # Чтобы не обрезать слово в начале нового куска, сдвигаем до пробела
        if start < text_len:
            space_idx = text.find(' ', start)
            if space_idx != -1:
                start = space_idx + 1

    return chunks


def _clean_text(text: str) -> str:
    """Базовая очистка: удаляем HTML-комментарии и мусор."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if re.search(r"\.{4,}", line):
            continue
        if re.match(r"^[\s\.\d]+$", line) and len(line) > 10:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _generate_chunk_id(file_hash: str, text: str, position: int) -> str:
    raw_id = f"{file_hash}:{position}:{text}"
    return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]