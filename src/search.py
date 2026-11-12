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
    # МЕНЯЕМ 1000 НА 10000, чтобы захватить все куски
    response = es.search(index=config.ES_INDEX_NAME, body={"query": {"match_all": {}}, "size": 10000})
    
    texts_to_embed = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        _chunks_data.append(source)
        texts_to_embed.append(f"passage: {source['text']}")
        
    print(f"Найдено чанков: {len(_chunks_data)}. Вычисление эмбеддингов...")
    model = get_semantic_model()
    embeddings = model.encode(texts_to_embed, normalize_embeddings=True)
    _chunks_embeddings = np.array(embeddings)
    print("Эмбеддинги готовы")
def reset_semantic_cache():
    """Сбрасывает кэш, чтобы новые файлы попали в поиск."""
    global _chunks_data, _chunks_embeddings
    _chunks_data = []
    _chunks_embeddings = None
    print("Кэш семантического поиска сброшен.")

def semantic_search(query: str, top_k: int = 5, threshold: float = 0.75) -> list[SearchResult]:
    """Поиск по смыслу с порогом отсечения."""
    load_embeddings()
    if len(_chunks_data) == 0:
        return []

    model = get_semantic_model()
    query_embedding = model.encode(f"query: {query}", normalize_embeddings=True)
    
    similarities = np.dot(_chunks_embeddings, query_embedding)
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    # ПРОВЕРКА ПОРОГА (Пункт 1)
    # Если лучший результат ниже порога, значит вопрос не по теме
    if len(top_indices) > 0 and similarities[top_indices[0]] < threshold:
        return [] # Возвращаем пустоту, экран покажет "ничего не найдено"

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
            score=float(similarities[idx])
        ))
    return results

# гибридный поиск
def hybrid_search(query: str, top_k: int = 5) -> list[SearchResult]:
    """Связка: берет результаты от поиска по словам и по смыслу, объединяет их через RRF."""
    words_results = search(query, top_k=top_k*2)
    sem_results = semantic_search(query, top_k=top_k*2)
    
    rrf_scores = {}
    chunk_map = {}
    k_rrf = 60 
    
    for rank, res in enumerate(words_results):
        rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + 1.0 / (k_rrf + rank + 1)
        chunk_map[res.chunk_id] = res
        
    # Начисляем баллы за позицию в результатах поиска по смыслу
    for rank, res in enumerate(sem_results):
        rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + 1.0 / (k_rrf + rank + 1)
        if res.chunk_id not in chunk_map:
            chunk_map[res.chunk_id] = res
            
    # Сортируем по итоговому баллу RRF
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:top_k]
    
    final_results = []
    for cid in sorted_ids:
        res = chunk_map[cid]
        res.score = rrf_scores[cid]
        final_results.append(res)
        
    return final_results