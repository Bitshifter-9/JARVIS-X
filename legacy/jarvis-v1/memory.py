import chromadb
from sentence_transformers import SentenceTransformer

client = chromadb.Client()
collection = client.get_or_create_collection("jarvis_memory")

embedder = SentenceTransformer("all-MiniLM-L6-v2")


def store_memory(text):
    emb = embedder.encode(text).tolist()
    collection.add(
        embeddings=[emb],
        documents=[text],
        ids=[str(hash(text))]
    )


def recall_memory(query, k=3):
    emb = embedder.encode(query).tolist()
    res = collection.query(query_embeddings=[emb], n_results=k)
    return res["documents"][0] if res["documents"] else []