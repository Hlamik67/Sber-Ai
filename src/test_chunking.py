import logging
from pathlib import Path
import config
from ingest import ingest_file
from chunking import chunk_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Берем первый попавшийся файл из data/raw
files = list(config.DATA_DIR.glob("*"))
if not files:
    print("В папке data/raw нет файлов!")
    exit(1)

test_path = files[0]
print(f"Загружаем документ: {test_path.name}")

# 1. Извлекаем текст в память
doc = ingest_file(test_path)
if not doc:
    print("Не удалось обработать файл.")
    exit(1)

print(f"Документ: {doc.file_name}")
print(f"Найдено секций: {len(doc.sections)}")

# 2. Нарезаем
chunks = chunk_document(doc, chunk_size=1000, overlap=150)
print(f"Всего кусков: {len(chunks)}\n")

for i, chunk in enumerate(chunks):
    print(f"========== КУСОК {i + 1} из {len(chunks)} ==========")
    print(f"ID: {chunk.chunk_id}")
    print(f"Стр: {chunk.page_number} | Раздел: {' > '.join(chunk.section_path) if chunk.section_path else 'Не указан'}")
    print("-" * 50)
    print(chunk.text)
    print("\n")

    if i >= 2:  # Показываем только 3 куска для теста
        print(f"... (и еще {len(chunks) - 3} кусков скрыто)")
        break