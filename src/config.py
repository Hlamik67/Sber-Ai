"""Конфигурация пайплайна ingest."""

from pathlib import Path

# --- Базовый путь ---
# Вычисляем корень проекта (папка Sber-Ai), чтобы пути не зависели от того, откуда запущен скрипт
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Пути ---
DATA_DIR = BASE_DIR / "data" / "raw"
INGEST_OUTPUT_DIR = BASE_DIR / "ingest_output"

# --- Поддерживаемые форматы ---
SUPPORTED_EXTENSIONS = {".md", ".pdf", ".docx"}

# --- Кодировки ---
TEXT_ENCODING = "utf-8"

# --- OCR (пригодится для PDF позже) ---
OCR_MIN_CHARS = 50
OCR_LANGS = "rus+eng"

# --- PDF ---
HEADER_FOOTER_LINES = 2
HEADER_FOOTER_THRESHOLD = 0.7
COLUMN_SPLIT_RATIO = 0.5
PDF_HEADING_SIZE_FACTOR = 1.15

# --- Общее ---
MAX_FILE_SIZE_MB = 50

# --- Elasticsearch ---
ES_HOST = "http://localhost:9200"
ES_INDEX_NAME = "documents"