import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from google import genai
from google.genai import types as genai_types
from db.models import Transaction
from config.settings import settings
import structlog

logger = structlog.get_logger()

_genai_client = genai.Client(api_key=settings.gemini_api_key)

# Relevance cutoff -- results with distance above this are discarded
MAX_DISTANCE_THRESHOLD = 1.2


class DocumentEmbeddingFunction(EmbeddingFunction):
    """Embeds documents for storage using RETRIEVAL_DOCUMENT task type."""
    def __call__(self, input: Documents) -> Embeddings:
        result = _genai_client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=input,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
        )
        return [e.values for e in result.embeddings]


class QueryEmbeddingFunction(EmbeddingFunction):
    """Embeds search queries using RETRIEVAL_QUERY task type."""
    def __call__(self, input: Documents) -> Embeddings:
        result = _genai_client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=input,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
        )
        return [e.values for e in result.embeddings]


chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)

# Storage collection uses document embedding
collection = chroma_client.get_or_create_collection(
    name="transactions_memory",
    embedding_function=DocumentEmbeddingFunction()
)

_query_embedder = QueryEmbeddingFunction()


def embed_transaction(txn: Transaction) -> None:
    """Embed a single transaction and store it in ChromaDB."""
    try:
        doc = (
            f"Transaction ID: {txn.id}. "
            f"Type: {txn.type}. "
            f"Category: {txn.category}. "
            f"Amount: {txn.amount}. "
            f"Date: {txn.txn_date}. "
            f"Description: {txn.description or 'No description'}. "
            f"Raw Input Context: {txn.raw_input or 'None'}."
        )

        collection.add(
            documents=[doc],
            metadatas=[{
                "user_id": str(txn.user_id),
                "type": txn.type,
                "category": txn.category or "unknown",
                "txn_date": str(txn.txn_date),
                "amount": float(txn.amount)
            }],
            ids=[f"txn_{txn.id}"]
        )
        logger.info("Transaction embedded in ChromaDB", txn_id=txn.id)
    except Exception as e:
        logger.error("Failed to embed transaction in ChromaDB", error=str(e), txn_id=txn.id)


def search_transactions(
    user_id: int, query: str, n_results: int = 5
) -> tuple[list[int], list[str]]:
    """Search for relevant transactions using asymmetric query embedding.
    
    Returns:
        Tuple of (transaction_ids, document_strings). Both lists are parallel.
    """
    try:
        # Embed query with RETRIEVAL_QUERY task type (asymmetric to stored documents)
        query_embedding = _query_embedder([query])

        results = collection.query(
            query_embeddings=query_embedding,
            n_results=n_results,
            where={"user_id": str(user_id)},
            include=["documents", "distances"]
        )

        if not results or not results.get("documents") or not results["documents"][0]:
            return [], []

        docs = results["documents"][0]
        distances = results["distances"][0]
        ids = results["ids"][0]

        # Filter out results beyond the relevance threshold
        filtered_ids = []
        filtered_docs = []
        for doc, dist, chroma_id in zip(docs, distances, ids):
            if dist <= MAX_DISTANCE_THRESHOLD:
                filtered_docs.append(doc)
                # Extract numeric ID from "txn_7" format
                try:
                    txn_id = int(chroma_id.split("_")[1])
                    filtered_ids.append(txn_id)
                except (IndexError, ValueError):
                    filtered_ids.append(-1)
            else:
                logger.debug("Discarded low-relevance result", distance=dist)

        return filtered_ids, filtered_docs
    except Exception as e:
        logger.error("Semantic search failed", error=str(e), user_id=user_id)
        return [], []
