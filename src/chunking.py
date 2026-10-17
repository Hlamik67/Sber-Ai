"""Нарезка документа на куски (чанки) с перекрытием."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

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
        clean_text = _clean_toc(doc.text)
        page_chunks = _split_text(
            text=clean_text,
            chunk_size=chunk_size,
            overlap=overlap,
            doc=doc,
            section_path=[],
            page_number=None,
            start_offset=0
        )
        chunks.extend(page_chunks)

    return chunks


def _clean_toc(text: str) -> str:
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
    chunks = []

    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size

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

        start = end - overlap
        if start < 0:
            start = 0
        if end == start + overlap:
            break

    return chunks


def _generate_chunk_id(file_hash: str, text: str, position: int) -> str:
    raw_id = f"{file_hash}:{position}:{text}"
    return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]