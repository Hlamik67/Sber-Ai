"""Главный скрипт: загрузка папки data/raw, нарезка и индексация."""

import logging
from ingest import process_data_folder
from chunking import chunk_document
from index import index_chunks

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("main")

if __name__ == "__main__":
    logger.info("=== Старт обработки папки data/raw ===")

    # 1. Извлекаем текст (все в памяти)
    docs = process_data_folder()

    if not docs:
        logger.error("Нет документов для обработки. Проверьте папку data/raw.")
        exit(1)

    total_chunks = 0

    # 2. Нарезаем и закидываем в Elasticsearch
    for doc in docs:
        logger.info("Нарезка документа: %s", doc.file_name)
        chunks = chunk_document(doc)

        if chunks:
            logger.info("  -> Нарезано: %d кусков", len(chunks))
            index_chunks(chunks)
            total_chunks += len(chunks)
        else:
            logger.warning("  -> Не удалось нарезать документ!")

    logger.info("=== Готово! Всего проиндексировано кусков: %d ===", total_chunks)