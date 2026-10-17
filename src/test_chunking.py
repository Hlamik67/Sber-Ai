import logging
from pathlib import Path
from ingest import load_document
from chunking import chunk_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# 1. Загружаем ранее сохраненный документ (хэш берем из вывода ingest)
# Укажите ваш хэш, который вывелся в прошлый раз
FILE_HASH = "08a3cbd6b2b19a57020bea4a96090f0bc6ae0a363728694061b9244601a2dda0"

doc = load_document(FILE_HASH)
if not doc:
    print("Документ не найден в ingest_output!")
    exit(1)

print(f"Документ: {doc.file_name}, страниц: {len(doc.pages) if doc.pages else 0}")

# 2. Нарезаем
chunks = chunk_document(doc, chunk_size=1000, overlap=150)

print(f"Всего кусков: {len(chunks)}")

# 3. Смотрим первые 3 куска
for i, chunk in enumerate(chunks[:3]):
    print(f"\n--- Кусок {i + 1} ---")
    print(f"ID: {chunk.chunk_id}")
    print(f"Стр: {chunk.page_number} | Раздел: {chunk.section_path}")
    print(f"Длина: {len(chunk.text)} символов")
    print(f"Текст (начало): {chunk.text[:150]}...")
    print(f"Текст (конец): ...{chunk.text[-50:]}")

# 4. Проверка overlap (перекрытия)
if len(chunks) >= 2:
    print("\n--- Проверка перекрытия ---")
    end_of_first = chunks[0].text[-100:]
    start_of_second = chunks[1].text[:150]

    # Ищем, есть ли конец первого куска в начале второго
    if any(word in start_of_second for word in end_of_first.split() if len(word) > 4):
        print("✅ Перекрытие работает (слова из конца 1-го куска есть в начале 2-го)")
    else:
        print("❌ Перекрытие не работает")