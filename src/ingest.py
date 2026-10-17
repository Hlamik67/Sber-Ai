"""Извлечение текста из файлов (.md, .pdf, .docx).

Публичный API:
    ingest_file(path, is_duplicate=None, force=False) -> Document | None
    ingest_folder(dir_path, is_duplicate=None, force=False) -> list[Document]
    save_document(doc) -> Path
    load_document(file_hash) -> Document | None
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
import pymupdf  # или import fitz, если версия pymupdf старая
import py3langid as langid

import ftfy

import config

logger = logging.getLogger("ingest")

# ============================================================
# Модель данных
# ============================================================
@dataclass
class Page:
    page_number: int
    text: str
    section_path: list[str] = field(default_factory=list)

@dataclass
class Section:
    level: int
    title: str
    page_number: Optional[int] = None

@dataclass
class Document:
    file_name: str
    file_path: str
    file_type: str                       # "md" | "pdf" | "docx"
    file_hash: str
    ingested_at: str
    lang: Optional[str]
    used_ocr: bool
    metadata: dict
    pages: Optional[list[Page]]          # None для md/docx
    sections: list[Section]
    text: str                            # полный очищенный текст


# ============================================================
# Утилиты
# ============================================================
def _hash_file(path: Path) -> str:
    """sha256 файла — для глобальной дедупликации."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _detect_type(path: Path) -> str:
    """Определить тип по расширению."""
    return path.suffix.lower().lstrip(".")

def _is_hidden_or_temp(path: Path) -> bool:
    """Отсеять скрытые и временные файлы."""
    name = path.name
    if name.startswith("."):
        return True
    if name.startswith("~$"):           # временные файлы Word
        return True
    if name in {"Thumbs.db", ".DS_Store"}:
        return True
    return False

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_lang(text: str) -> Optional[str]:
    """Определить язык текста (ru или en)."""
    if not text or len(text.strip()) < 20:
        return None

    # Ограничиваем текст для скорости (первые 5000 символов достаточно)
    sample = text[:5000]

    try:
        lang, _ = langid.classify(sample)
        # Оставляем только ru и en, остальное помечаем как None
        if lang in ('ru', 'en'):
            return lang
        return lang  # вернем как есть (например 'de'), пусть chunking решает
    except Exception:
        return None

# ============================================================
# Публичный API
# ============================================================

def ingest_file(
    path: str | Path,
    is_duplicate: Optional[Callable[[str], bool]] = None,
    force: bool = False,
) -> Optional[Document]:
    """Обработать один файл.

    is_duplicate — колбэк, принимает file_hash, возвращает True, если уже
    есть в БД. Передаётся снаружи, чтобы ingest не знал про БД.
    """
    path = Path(path)

    if not path.exists():
        logger.error("Файл не найден: %s", path)
        return None

    if path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
        logger.warning("Неподдерживаемое расширение: %s", path.suffix)
        return None

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > config.MAX_FILE_SIZE_MB:
        logger.warning("Файл слишком большой (%.1f MB): %s", size_mb, path)
        return None

    file_hash = _hash_file(path)

    if not force and is_duplicate and is_duplicate(file_hash):
        logger.info("Дубликат, пропускаем: %s (%s)", path.name, file_hash[:8])
        return None

    file_type = _detect_type(path)
    logger.info("Обработка %s [%s]", path.name, file_type)

    try:
        if file_type == "md":
            doc = _extract_md(path, file_hash)
        elif file_type == "docx":
            doc = _extract_docx(path, file_hash)
        elif file_type == "pdf":
            doc = _extract_pdf(path, file_hash)
        else:
            logger.error("Неизвестный тип: %s", file_type)
            return None
    except Exception:
        logger.exception("Ошибка обработки %s", path)
        return None

    logger.info(
        "Готово: %s, символов=%d, разделов=%d, ocr=%s",
        path.name, len(doc.text), len(doc.sections), doc.used_ocr,
    )
    return doc


def ingest_folder(
    dir_path: str | Path,
    is_duplicate: Optional[Callable[[str], bool]] = None,
    force: bool = False,
) -> list[Document]:
    """Обработать все поддерживаемые файлы в папке (рекурсивно)."""
    dir_path = Path(dir_path)
    if not dir_path.exists() or not dir_path.is_dir():
        logger.error("Папка не найдена: %s", dir_path)
        return []

    results: list[Document] = []
    for path in sorted(dir_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
            continue
        if _is_hidden_or_temp(path):
            continue

        doc = ingest_file(path, is_duplicate=is_duplicate, force=force)
        if doc is not None:
            results.append(doc)

    logger.info("Папка обработана: успешно %d файлов", len(results))
    return results

# ============================================================
# Сохранение / загрузка JSON
# ============================================================

def save_document(doc: Document) -> Path:
    """Сохранить Document в ingest_output/<hash>.json."""
    config.INGEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.INGEST_OUTPUT_DIR / f"{doc.file_hash}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(doc), f, ensure_ascii=False, indent=2)
    return out_path


def load_document(file_hash: str) -> Optional[Document]:
    """Загрузить Document из JSON."""
    path = config.INGEST_OUTPUT_DIR / f"{file_hash}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    data["pages"] = [Page(**p) for p in data["pages"]] if data["pages"] else None
    data["sections"] = [Section(**s) for s in data["sections"]]
    return Document(**data)

# ============================================================
# MD
# ============================================================

def _extract_md(path: Path, file_hash: str) -> Document:
    """Извлечь текст из Markdown."""
    raw = path.read_text(encoding=config.TEXT_ENCODING, errors="replace")
    raw = ftfy.fix_text(raw)

    sections: list[Section] = []
    section_path: list[str] = []
    body_lines: list[str] = []

    for line in raw.splitlines():
        heading_match = _match_md_heading(line)
        if heading_match is not None:
            level, title = heading_match
            # обрезаем section_path до уровня-1, потом добавляем
            section_path = section_path[: level - 1] + [title]
            sections.append(Section(level=level, title=title, page_number=None))

        body_lines.append(line)

    text = "\n".join(body_lines)
    text = _clean_text(text)

    return Document(
        file_name=path.name,
        file_path=str(path.resolve()),
        file_type="md",
        file_hash=file_hash,
        ingested_at=_now_iso(),
        lang=_detect_lang(text),
        used_ocr=False,
        metadata={"size_bytes": path.stat().st_size},
        pages=None,                       # в MD нет страниц
        sections=sections,
        text=text,
    )


def _match_md_heading(line: str) -> Optional[tuple[int, str]]:
    """Вернуть (level, title) если строка — заголовок Markdown, иначе None."""
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return None

    hashes = 0
    for ch in stripped:
        if ch == "#":
            hashes += 1
        else:
            break

    if hashes == 0 or hashes > 6:
        return None
    # после # должен быть пробел
    if len(stripped) <= hashes or stripped[hashes] != " ":
        return None

    title = stripped[hashes:].strip().rstrip("#").strip()
    if not title:
        return None
    return hashes, title

# ============================================================
# Очистка текста (минимальная версия)
# ============================================================

def _clean_text(text: str) -> str:
    """Базовая нормализация и очистка от PDF-мусора."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\xa0", " ")

    # Убираем строки, которые состоят в основном из странных символов (кракозябры)
    # Если больше 30% символов в строке — это не буквы/цифры/пробелы/знаки препинания, удаляем строку
    cleaned_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        # Считаем количество "нормальных" символов
        normal_chars = sum(1 for c in line if c.isalnum() or c in " .,;:!?\"'()-—–/\\")
        # Если нормальных символов слишком мало, пропускаем строку
        if len(line) > 0 and (normal_chars / len(line)) < 0.7:
            continue

        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Склеиваем переносы слов (когда слово разорвано переносом строки)
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

    # Схлопываем лишние пробелы и переносы
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# DOCX
# ============================================================

def _extract_docx(path: Path, file_hash: str) -> Document:
    """Извлечь текст из DOCX с сохранением порядка параграфов и таблиц."""
    docx_doc = DocxDocument(str(path))

    sections: list[Section] = []
    section_path: list[str] = []
    text_blocks: list[str] = []

    # Обходим элементы тела документа в том порядке, в котором они лежат в файле
    for child in docx_doc.element.body:
        if child.tag.endswith('}p'):  # Параграф (w:p)
            para = Paragraph(child, docx_doc)
            text = para.text.strip()
            if not text:
                continue

            # Проверяем, является ли параграф заголовком
            style_name = para.style.name if para.style else ""
            if style_name.startswith("Heading"):
                try:
                    level = int(style_name.split(" ")[-1])
                except ValueError:
                    level = 1

                # Обрезаем section_path до нужного уровня и добавляем новый
                section_path = section_path[: level - 1] + [text]
                sections.append(Section(level=level, title=text))

            text_blocks.append(text)

        elif child.tag.endswith('}tbl'):  # Таблица (w:tbl)
            table = Table(child, docx_doc)
            md_table = _docx_table_to_markdown(table)
            if md_table:
                text_blocks.append(md_table)

    # Собираем всё в один текст и чистим
    full_text = "\n\n".join(text_blocks)
    full_text = _clean_text(full_text)

    return Document(
        file_name=path.name,
        file_path=str(path.resolve()),
        file_type="docx",
        file_hash=file_hash,
        ingested_at=_now_iso(),
        lang=_detect_lang(full_text),
        used_ocr=False,
        metadata={"size_bytes": path.stat().st_size},
        pages=None,  # У DOCX нет фиксированных страниц
        sections=sections,
        text=full_text,
    )


def _docx_table_to_markdown(table: Table) -> str:
    """Преобразовать таблицу DOCX в Markdown."""
    if not table.rows:
        return ""

    md_lines = []
    for i, row in enumerate(table.rows):
        cells = []
        for cell in row.cells:
            # Внутри ячейки может быть несколько параграфов — склеиваем их пробелом
            cell_text = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            # Экранируем вертикальные черты, чтобы не сломать таблицу
            cell_text = cell_text.replace("|", "\\|")
            cells.append(cell_text)

        md_lines.append("| " + " | ".join(cells) + " |")

        # После первой строки добавляем разделитель Markdown
        if i == 0:
            separator = "|" + "|".join(["---"] * len(cells)) + "|"
            md_lines.append(separator)

    return "\n".join(md_lines)

# ============================================================
# PDF
# ============================================================
def _remove_pdf_headers_footers(pages_data: list[Page]) -> None:
    """Удаляет повторяющиеся шапки/колонтитулы и номера страниц."""
    if len(pages_data) < 2:
        return

    from collections import Counter
    first_lines = []
    last_lines = []

    for page in pages_data:
        lines = [l.strip() for l in page.text.split("\n") if l.strip()]
        if lines:
            first_lines.append(lines[0])
            last_lines.append(lines[-1])

    threshold = len(pages_data) * 0.6
    common_first = {line for line, count in Counter(first_lines).items() if count >= threshold}
    common_last = {line for line, count in Counter(last_lines).items() if count >= threshold}

    # Регулярка для номеров страниц (просто число или римская цифра)
    page_num_re = re.compile(r"^\s*(?:\d+|[IVXLCDM]+)\s*$")

    for page in pages_data:
        lines = page.text.split("\n")
        new_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue

            # Если это первая/последняя строка и она в списке частых — удаляем
            if i == 0 and stripped in common_first:
                continue
            if i == len(lines) - 1 and stripped in common_last:
                continue

            # Если это номер страницы в начале или конце блока — удаляем
            if (i == 0 or i == len(lines) - 1) and page_num_re.match(stripped):
                continue

            new_lines.append(line)

        page.text = "\n".join(new_lines).strip()
def _extract_pdf(path: Path, file_hash: str) -> Document:
    """Извлечь текст из PDF, обрабатывая колонки и заголовки."""
    doc = pymupdf.open(str(path))
    pages_data: list[Page] = []
    sections: list[Section] = []

    # Будем хранить путь разделов сквозным образом через страницы
    current_section_path: list[str] = []
    used_ocr = False

    for page_num, page in enumerate(doc, start=1):
        # 1. Извлекаем блоки с координатами
        blocks = _extract_pdf_blocks(page)

        # 2. Сортируем блоки с учетом колонок
        sorted_blocks = _sort_pdf_blocks_by_columns(blocks, page.rect.width)

        page_text_lines = []

        # 3. Собираем текст страницы и ищем заголовки
        for block in sorted_blocks:
            text = block["text"].strip()
            if not text:
                continue

            # Эвристика заголовка
            if _is_pdf_heading(block, blocks):
                # Уровень: если предыдущий раздел был на этой же странице,
                # возможно это подзаголовок. Но для простоты ставим 1.
                level = 1
                sections.append(Section(level=level, title=text, page_number=page_num))
                current_section_path = [text]

            page_text_lines.append(text)

        page_text = "\n".join(page_text_lines)

        # TODO: Здесь позже добавим проверку на скан и OCR
        # if _page_needs_ocr(page_text):
        #     page_text = _ocr_page(page)
        #     used_ocr = True

        pages_data.append(
            Page(
                page_number=page_num,
                text=page_text,
                section_path=list(current_section_path)  # копия текущего пути
            )
        )

    _remove_pdf_headers_footers(pages_data)
    # Собираем полный текст
    full_text = "\n\n".join(p.text for p in pages_data)
    full_text = _clean_text(full_text)

    return Document(
        file_name=path.name,
        file_path=str(path.resolve()),
        file_type="pdf",
        file_hash=file_hash,
        ingested_at=_now_iso(),
        lang=_detect_lang(full_text),
        used_ocr=used_ocr,
        metadata={
            "size_bytes": path.stat().st_size,
            "page_count": len(pages_data)
        },
        pages=pages_data,
        sections=sections,
        text=full_text,
    )


def _extract_pdf_blocks(page: pymupdf.Page) -> list[dict]:
    """Достать блоки текста с координатами."""
    blocks = []
    # get_text("dict") дает словари с bbox (координатами) и шрифтами
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:  # 0 = текст, 1 = картинка (картинки игнорируем)
            continue

        block_text = []
        max_font_size = 0

        for line in block["lines"]:
            for span in line["spans"]:
                block_text.append(span["text"])
                if span["size"] > max_font_size:
                    max_font_size = span["size"]

        text = "".join(block_text).strip()
        if text:
            blocks.append({
                "text": text,
                "bbox": block["bbox"],  # (x0, y0, x1, y1)
                "font_size": max_font_size
            })
    return blocks


def _sort_pdf_blocks_by_columns(blocks: list[dict], page_width: float) -> list[dict]:
    """Разложить блоки по колонкам и отсортировать сверху вниз."""
    if not blocks:
        return []

    mid_x = page_width * config.COLUMN_SPLIT_RATIO

    left_col = []
    right_col = []
    full_width = []

    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        width = x1 - x0

        if width > (page_width * 0.6):
            full_width.append(b)
        elif x1 <= mid_x + 20:
            left_col.append(b)
        elif x0 >= mid_x - 20:
            right_col.append(b)
        else:
            full_width.append(b)

    left_col.sort(key=lambda b: b["bbox"][1])
    right_col.sort(key=lambda b: b["bbox"][1])
    full_width.sort(key=lambda b: b["bbox"][1])

    # Теперь собираем всё вместе по оси Y (сверху вниз).
    # Так как у нас есть широкие блоки, они могут разделять колонки.
    # Для простоты объединяем все блоки и сортируем по Y,
    # а при равенстве Y — по X (слева направо).
    all_blocks = left_col + right_col + full_width
    all_blocks.sort(key=lambda b: (round(b["bbox"][1]), b["bbox"][0]))

    return all_blocks


def _is_pdf_heading(block: dict, all_blocks: list[dict]) -> bool:
    """Эвристика: заголовок, если шрифт больше среднего, текст короткий и не заканчивается точкой."""
    if not all_blocks:
        return False

    sizes = [b["font_size"] for b in all_blocks if b["font_size"] > 0]
    if not sizes:
        return False

    sizes.sort()
    median_size = sizes[len(sizes) // 2]

    text = block["text"].strip()

    # 1. Текст не должен быть слишком длинным
    is_short = len(text) < 80

    # 2. Шрифт заметно крупнее
    is_big_font = block["font_size"] >= median_size * 1.1  # Снизили порог с 1.15 до 1.1

    # 3. Текст не заканчивается на точку, запятую или тире (обычно заголовки так не заканчиваются)
    no_punctuation_end = not text.endswith(('.', ',', ';', ':'))

    return is_short and is_big_font and no_punctuation_end

    # Медианный размер шрифта на странице
    sizes.sort()
    median_size = sizes[len(sizes) // 2]

    # Проверяем, что текст не слишком длинный (заголовки короткие)
    is_short = len(block["text"]) < 100

    return block["font_size"] >= median_size * config.PDF_HEADING_SIZE_FACTOR and is_short


# ============================================================
# Пакетная загрузка папки data
# ============================================================

def process_data_folder(save_json: bool = True) -> list[Document]:
    """Обработать все файлы из папки data и сохранить их в JSON.

    Возвращает список документов для дальнейшей нарезки и индексации.
    """
    from pathlib import Path
    import config

    data_dir = Path(config.DATA_DIR)

    if not data_dir.exists():
        logger.warning("Папка %s не существует", data_dir)
        return []

    docs = ingest_folder(data_dir)
    logger.info("Из папки %s загружено документов: %d", data_dir, len(docs))

    if save_json:
        for doc in docs:
            out_path = save_document(doc)
            logger.info("JSON сохранен: %s", out_path.name)

    return docs