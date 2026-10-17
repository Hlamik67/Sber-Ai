"""Экран сотрудника (Ксюша): загрузка документа и показ кусков.

Файл здесь не читается и не режется: он передается в src.ingest.ingest_file (Лиза Р).
Пока такой функции нет, работает mock с тестовыми кусками.

Контракт ingest_file(filename: str, data: bytes) -> dict:
  успех:  {"status": "ok", "filename": str,
           "chunks": [{"chunk_id": str, "text": str, "section": str, "page": int}]}
  ошибка: {"status": "error", "code": str, "message": str}
"""
import streamlit as st

ALLOWED = ["pdf", "docx", "txt"]
MAX_MB = 20

try:
    from src import ingest as _ingest
    ingest_file = getattr(_ingest, "ingest_file", None)
except ImportError:
    ingest_file = None
USING_MOCK = ingest_file is None


def mock_ingest(filename: str, data: bytes) -> dict:
    return {
        "status": "ok",
        "filename": filename,
        "chunks": [
            {"chunk_id": "mock_0001", "text": "Срок рассмотрения обращения составляет 15 рабочих дней.",
             "section": "3.2 Сроки", "page": 4},
            {"chunk_id": "mock_0002", "text": "Если условия не выполнены, заявление возвращается заявителю.",
             "section": "3.5 Возврат", "page": 5},
        ],
    }


def send(filename: str, data: bytes) -> dict:
    """Передает файл дальше и приводит любой сбой к одному формату ошибки."""
    try:
        result = (mock_ingest if USING_MOCK else ingest_file)(filename, data)
    except Exception as e:  # сбой на стороне нарезки: показываем понятную ошибку, а не трейс
        return {"status": "error", "code": "transfer_failed",
                "message": f"Файл не удалось передать в нарезку ({type(e).__name__})."}
    if not isinstance(result, dict) or "status" not in result:
        return {"status": "error", "code": "bad_response",
                "message": "Нарезка вернула ответ в неожиданном формате."}
    return result


st.set_page_config(page_title="Сбер ИИ", page_icon="📄")
st.title("Загрузка документа")
st.caption("Выберите файл. Он будет нарезан на фрагменты, ниже появятся фрагменты с источником.")
if USING_MOCK:
    st.info("Код нарезки ещё не подключён: показаны тестовые фрагменты.")

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
            st.warning("Файл принят, но кусков не получилось. Проверьте, что в документе есть текст.")
        else:
            st.success(f"Готово: получено кусков — {len(res['chunks'])}")
            for c in res["chunks"]:
                with st.container(border=True):
                    st.write(c["text"])
                    st.caption(
                        f"Файл: {res.get('filename', uploaded.name)} · Раздел: {c.get('section') or 'не указан'} · "
                        f"Страница: {c.get('page') if c.get('page') is not None else 'не указана'} · "
                        f"Номер куска: {c['chunk_id']}"
                    )

st.divider()
st.text_input("Вопрос", disabled=True, placeholder="Поиск появится на следующей неделе")