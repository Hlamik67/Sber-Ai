# Суть файла: запись кусков в Elasticsearch и выдача их обратно. Elasticsearch — это база, в которой лежат нарезанные куски документов и по которой идёт поиск.
"""Индексация кусков документов в Elasticsearch."""

"""Индексация кусков документов в Elasticsearch."""

import logging
from typing import List

from elasticsearch import Elasticsearch, helpers
from chunking import Chunk
import config

logger = logging.getLogger("index")


def get_es_client() -> Elasticsearch:
    """Создать клиент Elasticsearch с увеличенным таймаутом."""
    return Elasticsearch(
        config.ES_HOST,
        request_timeout=60,
        retry_on_timeout=True,
        max_retries=3
    )


def create_index_if_not_exists(es: Elasticsearch) -> None:
    """Создать индекс с настройками для русского и английского языков."""
    mapping = {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "file_name": {"type": "keyword"},
            "text": {
                "type": "text",
                "analyzer": "russian"
            },
            "section_path": {
                "type": "text",
                "analyzer": "russian"
            },
            "page_number": {"type": "integer"}
        }
    }

    try:
        es.indices.create(index=config.ES_INDEX_NAME, mappings=mapping)
        logger.info("Индекс %s создан", config.ES_INDEX_NAME)
    except Exception as e:
        # Если индекс уже существует, ES выкинет ошибку. Это нормально.
        if "resource_already_exists_exception" in str(e):
            logger.info("Индекс %s уже существует", config.ES_INDEX_NAME)
        else:
            logger.error("Ошибка создания индекса: %s", e)
            raise


def index_chunks(chunks: List[Chunk]) -> None:
    """Записать куски в Elasticsearch."""
    es = get_es_client()
    create_index_if_not_exists(es)

    actions = []
    for chunk in chunks:
        actions.append({
            "_index": config.ES_INDEX_NAME,
            "_id": chunk.chunk_id,
            "_source": {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "file_name": chunk.file_name,
                "text": chunk.text,
                "section_path": chunk.section_path,
                "page_number": chunk.page_number
            }
        })

    success, failed = helpers.bulk(es, actions, stats_only=True)
    logger.info("Проиндексировано: %d кусков. Ошибок: %d", success, failed)