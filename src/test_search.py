import logging
from search import search

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Задаем вопросы, которые есть в нашем PDF
queries = [
    "Что такое первичный ключ?",
    "Зачем нужна нормализация баз данных?",
    "Какие команды Git используются для работы?",
]

for query in queries:
    print(f"\n{'=' * 50}")
    print(f"❓ ВОПРОС: {query}")
    print(f"{'=' * 50}")

    results = search(query, top_k=2)

    if not results:
        print("Ничего не найдено.")
        continue

    for i, res in enumerate(results, 1):
        print(f"\n--- Результат {i} (Score: {res.score:.2f}) ---")
        print(f"Файл: {res.file_name}")
        print(f"Стр: {res.page_number} | Раздел: {' > '.join(res.section_path) if res.section_path else 'Без раздела'}")
        print(f"ID куска: {res.chunk_id}")

        # Выводим текст (первые 300 символов)
        preview = res.text.replace('\n', ' ')[:300]
        print(f"Текст: {preview}...")