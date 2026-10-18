import logging
from pathlib import Path
import config
from ingest import load_document
from chunking import chunk_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def get_latest_document():
    """Найти последний сохраненный JSON и загрузить его."""
    json_files = list(config.INGEST_OUTPUT_DIR.glob("*.json"))
    if not json_files:
        return None

    latest_file = max(json_files, key=lambda p: p.stat().st_mtime)
    file_hash = latest_file.stem
    print(f"Загружаем документ: {latest_file.name}")
    return load_document(file_hash)


doc = get_latest_document()
if not doc:
    print("В папке ingest_output нет файлов! Сначала запустите ingest.")
    exit(1)

print(f"Документ: {doc.file_name}")
print(f"Страниц: {len(doc.pages) if doc.pages else 'нет (MD/DOCX)'}")

# Нарезаем
chunks = chunk_document(doc, chunk_size=1000, overlap=150)
print(f"Всего кусков: {len(chunks)}\n")

for i, chunk in enumerate(chunks):
    print(f"========== КУСОК {i + 1} из {len(chunks)} ==========")
    print(f"ID: {chunk.chunk_id}")
    print(f"Файл: {chunk.file_name}")
    print(f"Стр: {chunk.page_number} | Раздел: {' > '.join(chunk.section_path) if chunk.section_path else 'Не указан'}")
    print(f"Позиция в тексте: {chunk.start_char} - {chunk.end_char} (длина: {len(chunk.text)} символов)")
    print("-" * 50)
    print(chunk.text)  # Выводим ПОЛНЫЙ текст куска
    print("\n")

    # Ограничитель, чтобы не заспамить консоль, если кусков много
    # (Можете убрать этот блок, если хотите увидеть вообще все куски)
    if i >= 4:
        print(f"... (и еще {len(chunks) - 5} кусков скрыто)")
        break

print("\n=== ПРОВЕРКА СЕКЦИЙ ИЗ INGEST ===")
if doc.sections:
    for s in doc.sections:
        print(f"Уровень: {s.level}, Название: '{s.title}'")
else:
    print("Список sections ПУСТ! ingest.py не нашел заголовков.")