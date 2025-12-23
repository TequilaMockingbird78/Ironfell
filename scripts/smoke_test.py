from sentence_transformers import SentenceTransformer
import chromadb

DB_PATH = "chroma_db"

def main():
    print("Loading embedding model...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    print("Embedding test strings...")
    vectors = model.encode([
        "Elves govern the Verdant Canonate of Lethariel.",
        "The Free Sands are a human-dominated desert corridor."
    ])
    print("Embedding dimension:", len(vectors[0]))

    print("Initializing persistent Chroma DB...")
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection("smoke_test")

    collection.add(
        ids=["doc1", "doc2"],
        documents=[
            "Elves govern the Verdant Canonate of Lethariel.",
            "The Free Sands are a human-dominated desert corridor."
        ],
        metadatas=[
            {"type": "region", "name": "Lethariel"},
            {"type": "region", "name": "Free Sands"}
        ]
    )

    print("Querying database...")
    results = collection.query(
        query_texts=["Where do elves rule?"],
        n_results=2
    )

    print("Results:")
    for doc in results["documents"][0]:
        print("-", doc)

if __name__ == "__main__":
    main()
