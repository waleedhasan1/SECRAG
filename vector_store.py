"""Embedding generation and ChromaDB vector store for SEC filing chunks."""

import hashlib
import logging
import os
import time

import chromadb
import openai

from chunker import Chunk
from config import (
    CHROMA_COLLECTION_NAME,
    OPENAI_EMBEDDING_BATCH_SIZE,
    OPENAI_EMBEDDING_MODEL,
    VECTORDB_DIR,
)

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Wraps OpenAI embeddings API with batching and retry logic."""

    def __init__(
        self,
        model: str = OPENAI_EMBEDDING_MODEL,
        batch_size: int = OPENAI_EMBEDDING_BATCH_SIZE,
        max_retries: int = 5,
    ):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set. "
                "Set it with: export OPENAI_API_KEY=sk-..."
            )
        self._client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.batch_size = batch_size
        self.max_retries = max_retries

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts with batching and backoff."""
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            embeddings = self._embed_with_retry(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        """Embed a single batch with exponential backoff on rate limits."""
        delay = 5.0
        for attempt in range(self.max_retries):
            try:
                response = self._client.embeddings.create(
                    input=texts, model=self.model
                )
                return [item.embedding for item in response.data]
            except openai.RateLimitError:
                if attempt == self.max_retries - 1:
                    raise
                logger.warning(
                    "Rate limited, retrying in %.0fs (attempt %d/%d)",
                    delay, attempt + 1, self.max_retries,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
            except openai.APIError as e:
                if attempt == self.max_retries - 1:
                    raise
                logger.warning(
                    "API error: %s, retrying in %.0fs (attempt %d/%d)",
                    e, delay, attempt + 1, self.max_retries,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        return []  # unreachable


def _chunk_id(chunk: Chunk) -> str:
    """Generate deterministic ID: {ticker}_{type}_{date}_{index:04d}_{md5_8chars}."""
    content_hash = hashlib.md5(chunk.text.encode("utf-8")).hexdigest()[:8]
    m = chunk.metadata
    return f"{m.ticker}_{m.filing_type}_{m.filing_date}_{m.chunk_index:04d}_{content_hash}"


class VectorStore:
    """ChromaDB-backed vector store for SEC filing chunks."""

    def __init__(self, persist_dir: str = VECTORDB_DIR):
        os.makedirs(persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder: EmbeddingClient | None = None

    def _get_embedder(self) -> EmbeddingClient:
        if self._embedder is None:
            self._embedder = EmbeddingClient()
        return self._embedder

    def add_chunks(self, chunks: list[Chunk]) -> int:
        """Add chunks to the store. Returns number of newly added chunks.

        Skips chunks whose IDs already exist (idempotent).
        """
        if not chunks:
            return 0

        # Generate IDs and check which are new
        ids = [_chunk_id(c) for c in chunks]
        existing = set(self._collection.get(ids=ids)["ids"])
        new_indices = [i for i, cid in enumerate(ids) if cid not in existing]

        if not new_indices:
            logger.info("All %d chunks already in store, skipping", len(chunks))
            return 0

        new_chunks = [chunks[i] for i in new_indices]
        new_ids = [ids[i] for i in new_indices]

        logger.info(
            "Embedding %d new chunks (%d already exist)",
            len(new_chunks), len(chunks) - len(new_chunks),
        )

        # Generate embeddings
        texts = [c.text for c in new_chunks]
        embedder = self._get_embedder()
        embeddings = embedder.embed(texts)

        # Prepare metadata for ChromaDB (flat dict, string/int/float values only)
        metadatas = []
        for c in new_chunks:
            m = c.metadata
            metadatas.append({
                "ticker": m.ticker,
                "company_name": m.company_name,
                "filing_type": m.filing_type,
                "filing_date": m.filing_date,
                "accession_number": m.accession_number,
                "section_path": m.section_path,
                "chunk_index": m.chunk_index,
                "source_file": m.source_file,
                "token_count": c.token_count,
            })

        # Add in batches (ChromaDB handles large adds, but let's be safe)
        batch_size = 500
        for i in range(0, len(new_ids), batch_size):
            end = i + batch_size
            self._collection.add(
                ids=new_ids[i:end],
                embeddings=embeddings[i:end],
                documents=texts[i:end],
                metadatas=metadatas[i:end],
            )

        return len(new_chunks)

    def delete_filing(self, ticker: str, filing_type: str, filing_date: str) -> int:
        """Delete all chunks for a specific filing. Returns count deleted."""
        # ChromaDB where filter for combined metadata match
        results = self._collection.get(
            where={
                "$and": [
                    {"ticker": {"$eq": ticker}},
                    {"filing_type": {"$eq": filing_type}},
                    {"filing_date": {"$eq": filing_date}},
                ]
            }
        )
        ids_to_delete = results["ids"]
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
            logger.info(
                "Deleted %d chunks for %s %s %s",
                len(ids_to_delete), ticker, filing_type, filing_date,
            )
        return len(ids_to_delete)

    def get_stats(self) -> dict:
        """Return collection stats for logging."""
        count = self._collection.count()
        return {
            "collection": CHROMA_COLLECTION_NAME,
            "total_chunks": count,
        }
