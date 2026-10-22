"""Экран сотрудника (Ники): загрузка документа и вопрос.

Цепочка загрузки: файл -> временный файл -> ingest_file (Лиза Р) -> Document
                  -> chunk_document (Лиза Р) -> список Chunk -> index_chunks (Elasticsearch).
Цепочка вопроса:  поле вопроса -> search (Лиза Р) -> список кусков с оценкой и источником.

Экран в две колонки: слева загрузка (нарезанные фрагменты свернуты под стрелкой),
справа вопрос и найденные куски. Раскрытие стрелки слева не сдвигает поле вопроса.

Запуск из корня репозитория:
    docker compose up -d
    streamlit run app.py
"""
import html
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
from search import search as search_chunks  # noqa: E402
from elasticsearch import ConnectionError as ESConnectionError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

ALLOWED = ["pdf", "docx", "txt"]
MAX_MB = getattr(config, "MAX_FILE_SIZE_MB", 20)
TOP_K = 5  # сколько кусков показываем по вопросу


# ---------------------------------------------------------------------------
# Логика: загрузка и поиск
# ---------------------------------------------------------------------------
def send(filename: str, data: bytes) -> dict:
    """Файл -> ingest_file -> chunk_document -> index_chunks -> формат экрана.

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

    # Кладем куски в Elasticsearch. Если база недоступна, куски все равно возвращаем,
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


def ask(question: str) -> dict:
    """Вопрос -> search (Лиза Р) -> формат экрана.

    Успех:  {"status": "ok", "results": [{chunk_id, text, file, section, page, score}]}
            results пустой, если ничего не нашлось.
    Ошибка: {"status": "error", "code": str, "message": str}
    """
    try:
        found = search_chunks(question, top_k=TOP_K)
    except Exception as e:
        return {"status": "error", "code": "search_failed",
                "message": f"Поиск не сработал ({type(e).__name__}: {e})."}
    return {
        "status": "ok",
        "results": [
            {
                "chunk_id": r.chunk_id,
                "text": r.text,
                "file": r.file_name,
                "section": " / ".join(r.section_path) if r.section_path else "",
                "page": r.page_number,
                "score": r.score,
            }
            for r in found
        ],
    }


def process_upload(uploaded) -> dict:
    """Проверки + отправка файла. Результат кладется в session_state,
    чтобы статус и фрагменты не пропадали, когда человек задает вопрос."""
    if uploaded is None:
        return {"messages": [("error", "Файл не выбран. Выберите документ и нажмите «Отправить».")],
                "chunks": []}
    if uploaded.size == 0:
        return {"messages": [("error", "Файл пустой.")], "chunks": []}
    if uploaded.size > MAX_MB * 1024 * 1024:
        return {"messages": [("error", f"Файл больше {MAX_MB} МБ.")], "chunks": []}

    with st.spinner("Отправляем и нарезаем файл…"):
        res = send(uploaded.name, uploaded.getvalue())

    if res["status"] == "error":
        return {"messages": [("error", res.get("message", "Файл не удалось нарезать."))], "chunks": []}

    chunks = res.get("chunks") or []
    filename = res.get("filename", uploaded.name)
    if not chunks:
        return {"messages": [("warning", "Файл принят, но кусков не получилось. Проверьте, что в документе "
                                         "есть текст (слишком короткий текст отбрасывается при нарезке).")],
                "chunks": []}
    if res.get("index_error"):
        messages = [("info", f"Получено кусков: {len(chunks)}"), ("error", res["index_error"])]
    else:
        messages = [("success", f"Готово: получено кусков — {len(chunks)}, сохранено в базе.")]
    return {"messages": messages, "chunks": chunks, "filename": filename}


# ---------------------------------------------------------------------------
# Оформление (зеленая тема)
# ---------------------------------------------------------------------------
CSS = """
:root {
    --g: #21A038;                      /* основной зелёный */
    --tint: rgba(33,160,56,.08);       /* лёгкая зелёная подложка: видна и на светлом, и на тёмном фоне */
    --tint-strong: rgba(33,160,56,.18);
    --line: rgba(33,160,56,.40);
    /* Зелёный текст: зелёный смешивается с цветом текста текущей темы.
       Светлая тема -> тёмно-зелёный, тёмная тема -> светло-зелёный. */
    --green-text: color-mix(in srgb, #21A038 62%, currentColor);
}

/* Фон приложения не перекрашиваем (его задаёт тема Streamlit), только добавляем зелёный оттенок сверху */
[data-testid="stAppViewContainer"] { background-image: linear-gradient(180deg, var(--tint-strong) 0%, transparent 55%); }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2rem; max-width: 1200px; }

.hero { background: linear-gradient(120deg, #0f6b2a 0%, #21A038 60%, #5cc77a 100%);
        border-radius: 18px; padding: 1.6rem 2rem; margin-bottom: 1.6rem;
        box-shadow: 0 6px 18px rgba(15,107,42,.25); }
.hero h1 { color: #ffffff !important; margin: 0 0 .3rem 0 !important; padding: 0 !important;
           font-size: 2rem; }
.hero p { color: #e7f7ec !important; margin: 0 !important; font-size: 1.02rem; }

.section-title { font-size: 1.2rem; font-weight: 600; color: var(--green-text); margin: .2rem 0 .7rem; }
.step { display: inline-flex; width: 1.7rem; height: 1.7rem; align-items: center;
        justify-content: center; border-radius: 50%; background: var(--g); color: #fff;
        font-size: .95rem; margin-right: .5rem; }

button[kind="primary"],
button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--g) !important; border: 1px solid var(--g) !important;
    color: #ffffff !important; border-radius: 10px !important; font-weight: 600; }
button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    background: #0f6b2a !important; border-color: #0f6b2a !important; }

[data-testid="stFileUploaderDropzone"] { background: var(--tint); border: 1.5px dashed var(--g);
                                         border-radius: 12px; }
[data-testid="stForm"] { background: var(--tint); border: 1px solid var(--line); border-radius: 14px; }
div[data-baseweb="input"]:focus-within { border-color: var(--g) !important; }
[data-testid="stTextInput"] input:focus { box-shadow: none !important; }

[data-testid="stExpander"] { background: var(--tint); border: 1px solid var(--line); border-radius: 12px; }
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary p { color: var(--green-text) !important; font-weight: 600; }

/* Карточки: фон и рамка прозрачные-зелёные, текст берёт цвет темы */
.card { background: var(--tint); border: 1px solid var(--line); border-left: 5px solid var(--g);
        border-radius: 12px; padding: .9rem 1.1rem; margin: .6rem 0; }
.card-text { line-height: 1.55; font-size: .97rem; }
.card-meta { margin-top: .6rem; opacity: .65; font-size: .8rem; }
.match { display: flex; align-items: center; gap: .7rem; margin-bottom: .6rem; }
.match-bar { flex: 1; height: 8px; background: var(--tint-strong); border-radius: 999px; overflow: hidden; }
.match-fill { height: 100%; background: linear-gradient(90deg, #21A038, #5cc77a); border-radius: 999px; }
.match-label { font-size: .8rem; color: var(--green-text); font-weight: 600; white-space: nowrap; }

/* Плашки сообщений: цветной полупрозрачный фон + текст цвета темы, читаются в светлой и тёмной теме */
.note { border-radius: 12px; padding: .8rem 1rem; margin: .5rem 0; font-size: .95rem;
        line-height: 1.45; border: 1px solid; border-left-width: 5px; }
.note-success { background: rgba(33,160,56,.14);  border-color: rgba(33,160,56,.65); }
.note-error   { background: rgba(229,57,53,.14);  border-color: rgba(229,57,53,.70); }
.note-warning { background: rgba(255,179,0,.18);  border-color: rgba(255,179,0,.75); }
.note-info    { background: rgba(30,136,229,.14); border-color: rgba(30,136,229,.70); }
"""


def esc(text: str) -> str:
    """Безопасный текст для HTML-карточки: без тегов, переносы строк через <br>."""
    return html.escape(text).replace("\n", "<br>").replace("$", "&#36;")


def meta_line(file, section, page, chunk_id) -> str:
    return (f"Файл: {file} · Раздел: {section or 'не указан'} · "
            f"Страница: {page if page is not None else 'не указана'} · "
            f"Номер куска: {chunk_id}")


def card(text: str, meta: str, share: float | None = None, label: str = "") -> None:
    """Карточка с текстом куска. HTML держим в одной строке, чтобы Markdown его не ломал."""
    bar = ""
    if share is not None:
        bar = (f'<div class="match"><div class="match-bar"><div class="match-fill" '
               f'style="width:{share * 100:.0f}%"></div></div>'
               f'<span class="match-label">{esc(label)}</span></div>')
    st.markdown(
        f'<div class="card">{bar}<div class="card-text">{esc(text)}</div>'
        f'<div class="card-meta">{esc(meta)}</div></div>',
        unsafe_allow_html=True,
    )


def notice(level: str, text: str) -> None:
    """Плашка с сообщением (success / error / warning / info) в зелёной гамме, читается в светлой и тёмной теме."""
    st.markdown(f'<div class="note note-{level}">{esc(text)}</div>', unsafe_allow_html=True)


def section_title(number: str, title: str) -> None:
    st.markdown(f'<div class="section-title"><span class="step">{number}</span>{title}</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Экран
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Сбер ИИ", page_icon="📄", layout="wide")
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
st.markdown(
    '<div class="hero"><h1>Ассистент по документам</h1>'
    '<p>Загрузите документ и задайте вопрос: ответ найдём в документах и покажем, откуда он взят.</p></div>',
    unsafe_allow_html=True,
)

NOTICE = {lvl: (lambda text, lvl=lvl: notice(lvl, text)) for lvl in ("success", "error", "warning", "info")}

left, right = st.columns([1, 1.5], gap="large")

# Слева: загрузка. Фрагменты свернуты под стрелкой, поле вопроса справа от этого не сдвигается.
with left:
    section_title("1", "Документ")
    uploaded = st.file_uploader("Выберите файл (PDF, DOCX, TXT)", type=ALLOWED)
    if st.button("Отправить", type="primary"):
        st.session_state["upload"] = process_upload(uploaded)

    up = st.session_state.get("upload")
    if up:
        for level, text in up["messages"]:
            NOTICE[level](text)
        if up["chunks"]:
            with st.expander(f"Показать нарезанные фрагменты ({len(up['chunks'])})", expanded=False):
                for c in up["chunks"]:
                    card(c["text"], meta_line(up["filename"], c["section"], c["page"], c["chunk_id"]))

# Справа: вопрос и найденные куски.
with right:
    section_title("2", "Вопрос")
    with st.form("ask_form"):
        question = st.text_input("Ваш вопрос", placeholder="Например: какой срок рассмотрения обращения?")
        asked = st.form_submit_button("Найти", type="primary")

    if asked:
        if not question.strip():
            st.session_state["answer"] = {"error": "Введите вопрос."}
        else:
            with st.spinner("Ищем…"):
                st.session_state["answer"] = {"question": question.strip(), "ans": ask(question.strip())}

    ans_state = st.session_state.get("answer")
    if ans_state:
        if ans_state.get("error"):
            notice("error", ans_state["error"])
        else:
            ans = ans_state["ans"]
            st.caption(f"Ваш вопрос: «{ans_state['question']}»")
            if ans["status"] == "error":
                notice("error", ans.get("message", "Поиск не сработал."))
            elif not ans["results"]:
                # Заглушка: подробные причины (файл не прочитался, нет общих слов, база пуста)
                # появятся в коде следующей недели.
                notice("warning", "Ничего не найдено. Возможно, в вопросе нет слов из документов, "
                                  "или в базе ещё нет документов.")
            else:
                notice("success", f"Найдено фрагментов: {len(ans['results'])}")
                top = ans["results"][0]["score"] or 1.0
                for r in ans["results"]:
                    share = max(0.0, min(1.0, r["score"] / top))
                    card(
                        r["text"],
                        meta_line(r["file"], r["section"], r["page"], r["chunk_id"]),
                        share=share,
                        label=f"Насколько подошёл: {share:.0%} (оценка {r['score']:.2f})",
                    )