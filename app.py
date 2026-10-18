"""Экран сотрудника (Ники): загрузка документа и показ кусков.

Цепочка: загруженный файл -> временный файл -> ingest_file (Лиза Р) -> Document
         -> chunk_document (Лиза Р) -> список Chunk -> index_chunks (Elasticsearch)
         -> показ кусков на экране.

Запуск из корня репозитория:
    docker compose up -d
    streamlit run app.py
"""
import logging
import sys
import tempfile
from pathlib import Path

import streamlit as st

# Код Лизы импортирует соседние модули без префикса (import config, from ingest import ...),
# поэтому добавляем src в путь и импортируем так же, как это делает её run_all_from_data.py.
sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
from ingest import ingest_file  # noqa: E402
from chunking import chunk_document  # noqa: E402
from index import index_chunks  # noqa: E402
from elasticsearch import ConnectionError as ESConnectionError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

ALLOWED = ["pdf", "docx", "txt"]
MAX_MB = getattr(config, "MAX_FILE_SIZE_MB", 20)


def send(filename: str, data: bytes) -> dict:
    """Файл -> ingest_file -> chunk_document -> формат экрана.

    Успех:  {"status": "ok", "filename": str, "index_error": str | None,
             "chunks": [{chunk_id, text, section, page}]}
            index_error заполнен, если куски нарезались, но в базу не сохранились.
    Ошибка: {"status": "error", "code": str, "message": str}
    """
    name = Path(filename).name

    # ingest у Лизы знает md/pdf/docx. Если txt не поддержан, но md поддержан,
    # сохраняем txt как md: простой текст обрабатывается как markdown.
    supported = {e.lower() for e in config.SUPPORTED_EXTENSIONS}
    if Path(name).suffix.lower() == ".txt" and ".txt" not in supported and ".md" in supported:
        name = Path(name).stem + ".md"

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            doc = ingest_file(path)  # возвращает Document или None при любой проблеме
            if doc is None:
                return {"status": "error", "code": "ingest_failed",
                        "message": "Не удалось прочитать файл: неподдерживаемый формат, "
                                   "слишком большой размер или ошибка чтения. "
                                   "Подробности смотрите в терминале, где запущен streamlit."}
            chunks = chunk_document(doc)
    except Exception as e:
        return {"status": "error", "code": "transfer_failed",
                "message": f"Файл не удалось обработать ({type(e).__name__}: {e})."}

    # Кладем куски в Elasticsearch. Если база недоступна, куски все равно показываем,
    # но с явной ошибкой: по плану недели они должны лежать в базе.
    index_error = None
    if chunks:
        try:
            index_chunks(chunks)
        except ESConnectionError:
            index_error = ("База недоступна: куски не сохранены. "
                           "Запущен ли Elasticsearch (docker compose up -d)?")
        except Exception as e:
            index_error = f"Куски не удалось сохранить в базу ({type(e).__name__}: {e})."

    return {
        "status": "ok",
        "filename": filename,
        "index_error": index_error,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "section": " / ".join(c.section_path) if c.section_path else "",
                "page": c.page_number,
            }
            for c in chunks
        ],
    }


st.set_page_config(page_title="Сбер ИИ", page_icon="📄")
st.title("Загрузка документа")
st.caption("Выберите файл. Он будет нарезан на фрагменты, ниже появятся фрагменты с источником.")

uploaded = st.file_uploader("Файл документа", type=ALLOWED)
if st.button("Отправить", type="primary"):
    if uploaded is None:
        st.error("Файл не выбран. Выберите документ и нажмите «Отправить».")
    elif uploaded.size == 0:
        st.error("Файл пустой.")
    elif uploaded.size > MAX_MB * 1024 * 1024:
        st.error(f"Файл больше {MAX_MB} МБ.")
    else:
        with st.spinner("Отправляем и нарезаем файл…"):
            res = send(uploaded.name, uploaded.getvalue())
        if res["status"] == "error":
            st.error(res.get("message", "Файл не удалось нарезать."))
        elif not res.get("chunks"):
            st.warning("Файл принят, но кусков не получилось. Проверьте, что в документе есть текст "
                       "(слишком короткий текст отбрасывается при нарезке).")
        else:
            if res.get("index_error"):
                st.info(f"Получено кусков: {len(res['chunks'])}")
                st.error(res["index_error"])
            else:
                st.success(f"Готово: получено кусков — {len(res['chunks'])}, сохранено в базе.")
            for c in res["chunks"]:
                with st.container(border=True):
                    st.text(c["text"])
                    st.caption(
                        f"Файл: {res.get('filename', uploaded.name)} · Раздел: {c.get('section') or 'не указан'} · "
                        f"Страница: {c.get('page') if c.get('page') is not None else 'не указана'} · "
                        f"Номер куска: {c['chunk_id']}"
                    )

st.divider()
st.text_input("Вопрос", disabled=True, placeholder="Поиск появится на следующей неделе")