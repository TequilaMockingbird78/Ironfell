import os
import glob
import chromadb
from sentence_transformers import SentenceTransformer

DB_PATH = "chroma_db"
LORE_ROOT = "lore"

model = SentenceTransformer("all-MiniLM-L6-v2")
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_or_create_collection("lore")

def parse_markdown_sections(text):
    sections = []
    current_header = None
    current_body = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_header:
                sections.append((current_header, "\n".join(current_body)))
            current_header = line.strip()
            current_body = []
        else:
            current_body.append(line)

    if current_header:
        sections.append((current_header, "\n".join(current_body)))

    return sections

def infer_metadata(filepath):
    parts = filepath.split(os.sep)
    category = parts[1]  # regions, factions, magic, etc.
    name = os.path.splitext(os.path.basename(filepath))[0]

    return {
        "type": category.rstrip("s"),
        "name": name,
        "canon": "hard"
    }

def ingest_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    sections = parse_markdown_sections(text)
    docs, metadatas, ids = [], [], []

    base_meta = infer_metadata(filepath)

    for i, (header, body) in enumerate(sections):
        content = f"{header}\n{body}".strip()
        if len(content) < 50:
            continue

        docs.append(content)
        meta = base_meta.copy()
        meta["section"] = header.replace("## ", "")
        meta["source_file"] = filepath
        metadatas.append(meta)
        ids.append(f"{filepath}:{i}")

    if docs:
        collection.add(documents=docs, metadatas=metadatas, ids=ids)

def main():
    md_files = glob.glob(f"{LORE_ROOT}/**/*.md", recursive=True)
    for path in md_files:
        print(f"Ingesting {path}")
        ingest_file(path)

if __name__ == "__main__":
    main()
