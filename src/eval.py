# Суть файла: подсчёт метрик (Precision@k и Recall) для поиска по словам. 
# Поиск по смыслу и гибрид пока отключены (заглушки).

import json
import os
from elasticsearch import Elasticsearch

# Путь к файлу с вопросами и настройки
QA_FILE = "data/qa.jsonl" 
K = 3
ES_HOST = "http://localhost:9200"
ES_INDEX = "documents"

def load_questions():
    """Читает файл qa.jsonl."""
    if not os.path.exists(QA_FILE):
        print(f"Ошибка: Не могу найти файл {QA_FILE}")
        return []

    questions = []
    with open(QA_FILE, 'r', encoding='utf-8-sig') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                questions.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Ошибка JSON на строке {line_num}.")
    return questions

def search_by_words(query: str, k: int) -> list[str]:
    """Поиск по словам напрямую через Elasticsearch."""
    try:
        es = Elasticsearch(ES_HOST)
        # Ищем совпадения по тексту
        res = es.search(index=ES_INDEX, body={"query": {"match": {"text": query}}, "size": k})
        return [hit["_source"]["chunk_id"] for hit in res["hits"]["hits"]]
    except Exception as e:
        print(f"Ошибка при подключении к базе (проверь Docker): {e}")
        return []

def search_by_semantic(query: str, k: int) -> list[str]:
    """Заглушка для поиска по смыслу."""
    return []

def search_hybrid(query: str, k: int) -> list[str]:
    """Заглушка для гибридного поиска."""
    return []

def evaluate_method(search_func, questions: list[dict], k: int, method_name: str):
    """Считает precision@k и recall."""
    print(f"\n--- Оценка метода: {method_name} ---")
    
    total_queries = 0
    sum_precision = 0.0
    sum_recall = 0.0
    
    for i, item in enumerate(questions, 1):
        query = item.get("question", "")
        correct_id = item.get("chunk_id", "")
        
        if not correct_id:
            print(f"[{i}] Пропуск: нет правильного chunk_id для вопроса '{query[:40]}...'")
            continue
            
        total_queries += 1
        found_ids = search_func(query, k)
        
        if correct_id in found_ids:
            precision = 1.0 / k
            recall = 1.0
            status = "Попадание"
        else:
            precision = 0.0
            recall = 0.0
            status = "Промах"
            
        sum_precision += precision
        sum_recall += recall
        
        print(f"[{i}] {status} | Вопрос: {query[:50]}...")
        if status == "Промах":
            print(f"    Ожидался ID: {correct_id}")
            print(f"    Получены ID: {found_ids}")
            
    if total_queries == 0:
        print("Нет вопросов для оценки.")
        return
        
    avg_precision = sum_precision / total_queries
    avg_recall = sum_recall / total_queries
    
    print(f"\nРезультаты для '{method_name}':")
    print(f"Всего вопросов оценено: {total_queries}")
    print(f"Precision@{k}: {avg_precision:.4f}")
    print(f"Recall@{k}: {avg_recall:.4f}")

def main():
    print("Запуск оценки качества поиска (eval.py)")
    questions = load_questions()
    print(f"Загружено вопросов: {len(questions)}")
    
    methods = [
        ("Поиск по словам (Elasticsearch)", search_by_words),
        ("Поиск по смыслу", search_by_semantic),
        ("Гибридный поиск", search_hybrid)
    ]
    
    for method_name, search_func in methods:
        evaluate_method(search_func, questions, K, method_name)

if __name__ == "__main__":
    main()