"""Экран сотрудника (Ники): загрузка документа и вопрос.

Цепочка загрузки: файл -> временный файл -> ingest_file (Лиза Р) -> Document
                  -> chunk_document (Лиза Р) -> список Chunk -> index_chunks (Elasticsearch).
Цепочка вопроса:  поле вопроса -> search (Лиза Р) -> список кусков с оценкой и источником.

Экран в две колонки: слева загрузка (нарезанные фрагменты свернуты под стрелкой),
справа вопрос, ответ с источником (файл, раздел, страница) и пояснение, если ничего не
найдено. Раскрытие стрелки слева не сдвигает поле вопроса.

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
from search import search as search_chunks, get_es_client  # noqa: E402
from elasticsearch import ConnectionError as ESConnectionError, NotFoundError  # noqa: E402

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
            # Elasticsearch обновляет поиск раз в секунду: просим обновить сразу,
            # чтобы только что загруженный файл был виден и в поиске, и в строке «В базе сейчас».
            get_es_client().indices.refresh(index=config.ES_INDEX_NAME)
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
    чтобы статус и фрагменты не пропадали, когда человек задает вопрос.

    failed=True значит, что выбранный файл не попал в базу (по любой причине).
    Это нужно, чтобы потом объяснить пустой ответ на вопрос."""
    if uploaded is None:
        return {"messages": [("error", "Файл не выбран. Выберите документ и нажмите «Отправить».")],
                "chunks": [], "failed": False}
    if uploaded.size == 0:
        return {"messages": [("error", "Файл пустой.")], "chunks": [], "failed": True,
                "filename": uploaded.name}
    if uploaded.size > MAX_MB * 1024 * 1024:
        return {"messages": [("error", f"Файл больше {MAX_MB} МБ.")], "chunks": [], "failed": True,
                "filename": uploaded.name}

    with st.spinner("Отправляем и нарезаем файл…"):
        res = send(uploaded.name, uploaded.getvalue())

    if res["status"] == "error":
        return {"messages": [("error", res.get("message", "Файл не удалось нарезать."))],
                "chunks": [], "failed": True, "filename": uploaded.name}

    chunks = res.get("chunks") or []
    filename = res.get("filename", uploaded.name)
    if not chunks:
        return {"messages": [("warning", "Файл принят, но кусков не получилось. Проверьте, что в документе "
                                         "есть текст (слишком короткий текст отбрасывается при нарезке).")],
                "chunks": [], "failed": True, "filename": filename}
    if res.get("index_error"):
        messages = [("info", f"Получено кусков: {len(chunks)}"), ("error", res["index_error"])]
        failed = True
    else:
        messages = [("success", f"Готово: получено кусков — {len(chunks)}, сохранено в базе.")]
        failed = False
    return {"messages": messages, "chunks": chunks, "filename": filename, "failed": failed}


# Причины, по которым поиск мог ничего не вернуть. level: цвет плашки на экране.
# Позже (неделя 2–8 ноября) эти фразы должны приходить из кода системы, а не из экрана.
REASONS = {
    "db_unavailable": {
        "level": "error", "title": "База недоступна",
        "message": "Поиск не смог обратиться к базе. Проверьте, что Elasticsearch запущен "
                   "(docker compose up -d), и повторите вопрос.",
    },
    "file_not_indexed": {
        "level": "error", "title": "Файл не попал в базу",
        "message": "Загруженный файл не сохранился в базе, поэтому искать в нём нельзя. "
                   "Причина указана слева у загрузки. Исправьте её и загрузите файл ещё раз.",
    },
    "db_empty": {
        "level": "info", "title": "База пока пустая",
        "message": "Искать негде: ни один документ ещё не загружен. "
                   "Загрузите документ слева и повторите вопрос.",
    },
    "no_common_words": {
        "level": "warning", "title": "Ничего не нашлось",
        "message": "Документы в базе есть, но слов из вашего вопроса в них не нашлось. "
                   "Поиск пока работает по словам: попробуйте спросить короче и словами из документа, "
                   "например «срок рассмотрения».",
    },
}


def explain_empty(last_upload: dict | None) -> dict:
    """Почему поиск ничего не вернул. Проверки идут по порядку, берется первая сработавшая.

    1) база недоступна; 2) последний загруженный файл не попал в базу
    (ошибка при загрузке или не все его куски найдены в индексе); 3) база пустая;
    4) база не пустая и работает: значит, в вопросе нет общих слов с документами.
    """
    try:
        es = get_es_client()
        if not es.ping():
            return REASONS["db_unavailable"]
        index_exists = bool(es.indices.exists(index=config.ES_INDEX_NAME))
        total = es.count(index=config.ES_INDEX_NAME)["count"] if index_exists else 0

        file_missing = bool(last_upload and last_upload.get("failed"))
        ids = [c["chunk_id"] for c in (last_upload or {}).get("chunks", [])]
        if ids and not file_missing:
            found = es.count(index=config.ES_INDEX_NAME,
                             query={"ids": {"values": ids}})["count"] if index_exists else 0
            file_missing = found < len(ids)
    except Exception:
        return REASONS["db_unavailable"]

    if file_missing:
        return REASONS["file_not_indexed"]
    if total == 0:
        return REASONS["db_empty"]
    return REASONS["no_common_words"]


def db_summary() -> dict:
    """Что лежит в базе: сколько кусков и из каких файлов.

    {"status": "ok", "total": int, "files": [(имя файла, число кусков), ...]}
    {"status": "unavailable"}, если база не отвечает.
    """
    try:
        es = get_es_client()
        resp = es.search(index=config.ES_INDEX_NAME, size=0, track_total_hits=True,
                         aggs={"files": {"terms": {"field": "file_name", "size": 50}}})
    except NotFoundError:  # индекса ещё нет: ничего не загружали
        return {"status": "ok", "total": 0, "files": []}
    except Exception:
        return {"status": "unavailable"}
    total = resp["hits"]["total"]["value"]
    files = [(b["key"], b["doc_count"]) for b in resp["aggregations"]["files"]["buckets"]]
    return {"status": "ok", "total": total, "files": files}


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

.answer-label { font-size: .8rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                color: var(--green-text); margin: .4rem 0 -.2rem; }
.card-answer { background: var(--tint-strong); border-left-width: 8px; }
.card-answer .card-text { font-size: 1.02rem; }
.card-answer .card-meta { opacity: .85; font-weight: 600; }

.dbbox { border: 1px dashed var(--line); border-radius: 12px; padding: .6rem .9rem; margin: .8rem 0;
         font-size: .9rem; line-height: 1.5; }
.dbbox b { color: var(--green-text); }

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


def card(text: str, meta: str, share: float | None = None, label: str = "",
         highlight: bool = False) -> None:
    """Карточка с текстом куска. HTML держим в одной строке, чтобы Markdown его не ломал.
    highlight=True: главный ответ (крупнее и ярче)."""
    bar = ""
    if share is not None:
        bar = (f'<div class="match"><div class="match-bar"><div class="match-fill" '
               f'style="width:{share * 100:.0f}%"></div></div>'
               f'<span class="match-label">{esc(label)}</span></div>')
    cls = "card card-answer" if highlight else "card"
    st.markdown(
        f'<div class="{cls}">{bar}<div class="card-text">{esc(text)}</div>'
        f'<div class="card-meta">{esc(meta)}</div></div>',
        unsafe_allow_html=True,
    )


def source_line(file, section, page, chunk_id) -> str:
    """Откуда взят ответ: файл, раздел, страница."""
    return (f"Источник: {file} · раздел: {section or 'не указан'} · "
            f"страница: {page if page is not None else 'не указана'} · номер куска: {chunk_id}")


def notice(level: str, text: str, title: str | None = None) -> None:
    """Плашка с сообщением (success / error / warning / info) в зелёной гамме, читается в светлой и тёмной теме."""
    head = f"<b>{esc(title)}</b><br>" if title else ""
    st.markdown(f'<div class="note note-{level}">{head}{esc(text)}</div>', unsafe_allow_html=True)


def db_box() -> None:
    """Строка «что сейчас лежит в базе»: видно, в каких документах идёт поиск."""
    info = db_summary()
    if info["status"] != "ok":
        body = "<b>В базе:</b> база недоступна"
    elif info["total"] == 0:
        body = "<b>В базе:</b> пока нет документов"
    else:
        files = info["files"]
        names = ", ".join(f"{esc(str(n))} ({c})" for n, c in files[:8])
        more = f" и ещё {len(files) - 8}" if len(files) > 8 else ""
        body = (f"<b>В базе сейчас:</b> кусков — {info['total']}. "
                f"Файлы (кусков в каждом): {names}{more}")
    st.markdown(f'<div class="dbbox">{body}</div>', unsafe_allow_html=True)


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
    # Ключ загрузчика меняется после каждой отправки: поле очищается, файл из него пропадает.
    uploader_key = f"uploader_{st.session_state.get('uploader_n', 0)}"
    uploaded = st.file_uploader("Выберите файл (PDF, DOCX, TXT)", type=ALLOWED, key=uploader_key)
    if st.button("Отправить", type="primary"):
        st.session_state["upload"] = process_upload(uploaded)
        if uploaded is not None:
            st.session_state["uploader_n"] = st.session_state.get("uploader_n", 0) + 1
            st.rerun()

    up = st.session_state.get("upload")
    if up:
        if up.get("filename"):
            st.caption(f"Последний отправленный файл: {up['filename']}")
        for level, text in up["messages"]:
            NOTICE[level](text)

    db_box()

    if up and up["chunks"]:
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
                ans = ask(question.strip())
                reason = None
                if ans["status"] == "ok" and not ans["results"]:
                    reason = explain_empty(st.session_state.get("upload"))
                st.session_state["answer"] = {"question": question.strip(), "ans": ans, "reason": reason}

    ans_state = st.session_state.get("answer")
    if ans_state:
        if ans_state.get("error"):
            notice("error", ans_state["error"])
        else:
            ans = ans_state["ans"]
            st.caption(f"Ваш вопрос: «{ans_state['question']}»")
            if ans["status"] == "error":
                notice("error", ans.get("message", "Поиск не сработал."), title="Поиск не сработал")
            elif not ans["results"]:
                # Причина вычислена в explain_empty: база недоступна / файл не попал в базу /
                # база пустая / нет общих слов с документами.
                reason = ans_state.get("reason") or REASONS["no_common_words"]
                notice(reason["level"], reason["message"], title=reason["title"])
            else:
                results = ans["results"]
                top = results[0]["score"] or 1.0
                best = results[0]
                st.markdown('<div class="answer-label">Ответ</div>', unsafe_allow_html=True)
                card(
                    best["text"],
                    source_line(best["file"], best["section"], best["page"], best["chunk_id"]),
                    share=1.0,
                    label=f"Насколько подошёл: 100% (оценка {best['score']:.2f})",
                    highlight=True,
                )
                others = results[1:]
                if others:
                    with st.expander(f"Другие найденные фрагменты ({len(others)})", expanded=False):
                        for r in others:
                            share = max(0.0, min(1.0, r["score"] / top))
                            card(
                                r["text"],
                                source_line(r["file"], r["section"], r["page"], r["chunk_id"]),
                                share=share,
                                label=f"Насколько подошёл: {share:.0%} (оценка {r['score']:.2f})",
                            )
