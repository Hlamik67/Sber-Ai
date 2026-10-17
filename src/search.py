# Суть файла: поиск по словам, поиск по смыслу и связка. Для экрана возвращает один лучший кусок или причину, почему пусто.

"""Поиск по базе знаний (Elasticsearch)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from elasticsearch import Elasticsearch
import config

logger = logging.getLogger("search")


@dataclass
class SearchResult:
    chunk_id: str
    document_id: str
    file_name: str
    text: str
    section_path: list[str]
    page_number: int | None
    score: float


def get_es_client() -> Elasticsearch:
    """Создать клиент Elasticsearch."""
    return Elasticsearch(config.ES_HOST)


def search(query: str, top_k: int = 5) -> list[SearchResult]:
    if not query.strip():
        return []

    es = get_es_client()

    # Более строгий поиск, чтобы фраза "первичный ключ" искалась именно вместе
    search_body = {
        "query": {
            "bool": {
                "should": [
                    {
                        "match_phrase": {
                            "text": {
                                "query": query,
                                "boost": 5.0  # Точная фраза — максимальный приоритет
                            }
                        }
                    },
                    {
                        "match": {
                            "text": {
                                "query": query,
                                "operator": "and" # Ищем только те куски, где есть ВСЕ слова из вопроса
                            }
                        }
                    },
                    {
                        "match": {
                            "section_path": {
                                "query": query,
                                "boost": 2.0
                            }
                        }
                    }
                ]
            }
        },
        "size": top_k,
        "_source": ["chunk_id", "document_id", "file_name", "text", "section_path", "page_number"]
    }

    try:
        response = es.search(index=config.ES_INDEX_NAME, body=search_body)
    except Exception as e:
        logger.error("Ошибка поиска в Elasticsearch: %s", e)
        return []

    results = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        results.append(SearchResult(
            chunk_id=source["chunk_id"],
            document_id=source["document_id"],
            file_name=source["file_name"],
            text=source["text"],
            section_path=source.get("section_path", []),
            page_number=source.get("page_number"),
            score=hit["_score"]
        ))

    return results