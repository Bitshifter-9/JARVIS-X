import os
import chromadb
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader

client = chromadb.Client()
collection = client.get_or_create_collection("jarvis_knowledge")
embedder = SentenceTransformer("all-MiniLM-L6-v2")


def read_file(path):
    if path.endswith(".pdf"):
        text = ""
        reader = PdfReader(path)
        for p in reader.pages:
            text += p.extract_text() or ""
        return text

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def index_knowledge(folder="knowledge"):
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        text = read_file(path)
        if not text.strip():
            continue

        emb = embedder.encode(text).tolist()
        collection.add(embeddings=[emb], documents=[text], ids=[f])


def search_knowledge(q, k=2):
    emb = embedder.encode(q).tolist()
    res = collection.query(query_embeddings=[emb], n_results=k)
    return "\n".join(res["documents"][0]) if res["documents"] else ""