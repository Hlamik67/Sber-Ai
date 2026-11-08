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

#Поиск по смыслу
import os
import numpy as np
from sentence_transformers import SentenceTransformer

_semantic_model = None
_chunks_data = []
_chunks_embeddings = None

def get_semantic_model():
    """Загружает модель в папку models (чтобы не засорять кеш системы)."""
    global _semantic_model
    if _semantic_model is None:
        # Создаем папку models, если ее нет
        os.makedirs("models", exist_ok=True)
        print("Загрузка модели e5 в папку models (это займет время при первом запуске)...")
        _semantic_model = SentenceTransformer("intfloat/multilingual-e5-base", cache_folder="models")
    return _semantic_model

def load_embeddings():
    global _chunks_data, _chunks_embeddings
    if _chunks_embeddings is not None:
        return

    print("Загрузка чанков из Elasticsearch для семантики...")
    es = get_es_client()
    response = es.search(index=config.ES_INDEX_NAME, body={"query": {"match_all": {}}, "size": 1000})
    
    texts_to_embed = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        _chunks_data.append(source)
        # Для модели e5 важен префикс "passage:"
        texts_to_embed.append(f"passage: {source['text']}")
        
    print(f"Найдено чанков: {len(_chunks_data)}. Вычисление эмбеддингов...")
    model = get_semantic_model()
    embeddings = model.encode(texts_to_embed, normalize_embeddings=True)
    _chunks_embeddings = np.array(embeddings)

def semantic_search(query: str, top_k: int = 5) -> list[SearchResult]:
    """Поиск по смыслу. Возвращает список объектов SearchResult, как и поиск по словам."""
    load_embeddings()
    if len(_chunks_data) == 0:
        return []

    model = get_semantic_model()
    query_embedding = model.encode(f"query: {query}", normalize_embeddings=True)
    
    # Считаем косинусное сходство
    similarities = np.dot(_chunks_embeddings, query_embedding)
    # Берем топ-k индексов
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    results = []
    for idx in top_indices:
        source = _chunks_data[idx]
        results.append(SearchResult(
            chunk_id=source["chunk_id"],
            document_id=source.get("document_id", ""),
            file_name=source.get("file_name", ""),
            text=source.get("text", ""),
            section_path=source.get("section_path", []),
            page_number=source.get("page_number"),
            score=float(similarities[idx]) # Оценка схожести
        ))
    return results  