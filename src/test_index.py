import logging
from ingest import load_document
from chunking import chunk_document
from index import index_chunks

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Хэш нашего PDF из предыдущих шагов
FILE_HASH = "08a3cbd6b2b19a57020bea4a96090f0bc6ae0a363728694061b9244601a2dda0"

print("1. Загрузка документа...")
doc = load_document(FILE_HASH)
if not doc:
    print("Документ не найден в ingest_output!")
    exit(1)

print("2. Нарезка на куски...")
chunks = chunk_document(doc)
print(f"   Нарезано: {len(chunks)} кусков")

print("3. Отправка в Elasticsearch...")
index_chunks(chunks)
print("Готово!")