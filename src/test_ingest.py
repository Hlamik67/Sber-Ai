import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from ingest import ingest_file, save_document

doc = ingest_file("../data/raw/test_data_py.pdf")   # путь от src/
if doc:
    print("--- text ---")
    print(doc.text)
    print("--- sections ---")
    for s in doc.sections:
        print(f"{'  ' * (s.level - 1)}[{s.level}] {s.title}")
    print("--- meta ---")
    print("hash:", doc.file_hash[:12])
    print("lang:", doc.lang)
    print("size:", doc.metadata)

    out = save_document(doc)
    print("saved to:", out)
else:
    print("файл не обработан")