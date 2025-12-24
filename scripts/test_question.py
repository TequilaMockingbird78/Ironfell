import chromadb

client = chromadb.PersistentClient(path="chroma_db")
collection = client.get_collection("lore")

results = collection.query(
    query_texts=[
        "What governs the semi-arid valley corridor with rivers and dwarven strongholds?"
    ],
    n_results=3
)

for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
    print("----")
    print(doc[:300])
    print("METADATA:", meta)
