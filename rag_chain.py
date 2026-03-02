"""RAG retrieval chain: embed question, retrieve chunks, generate answer with citations."""

import logging
import os
from dataclasses import dataclass, field

import openai

from config import OPENAI_CHAT_MODEL, RAG_TOP_K
from vector_store import VectorStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a financial analyst assistant. Answer the question based ONLY on the "
    "provided SEC filing excerpts. If the excerpts don't contain enough information, "
    "say so. Cite your sources using [Source N] notation matching the numbered "
    "excerpts. Be specific about which company and filing the information comes from."
)


@dataclass
class Source:
    ticker: str
    filing_type: str
    filing_date: str
    section_path: str
    relevance_rank: int


@dataclass
class RAGResponse:
    question: str
    answer: str
    sources: list[Source]
    model: str
    chunks_retrieved: int


class RAGChain:
    """Retrieval-augmented generation over SEC filing chunks in ChromaDB."""

    def __init__(self, vector_store: VectorStore, model: str = OPENAI_CHAT_MODEL):
        self._store = vector_store
        self._model = model
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set. "
                "Set it with: export OPENAI_API_KEY=sk-..."
            )
        self._llm = openai.OpenAI(api_key=api_key)

    def query(
        self,
        question: str,
        ticker: str | None = None,
        filing_type: str | None = None,
        top_k: int = RAG_TOP_K,
        chat_history: list[dict[str, str]] | None = None,
    ) -> RAGResponse:
        """Embed question, retrieve top-K chunks, generate an answer with citations."""

        # 1. Embed the question
        embedder = self._store._get_embedder()
        q_embedding = embedder.embed([question])[0]

        # 2. Build optional metadata filter
        where_filter = self._build_where(ticker, filing_type)

        # 3. Query ChromaDB
        query_kwargs: dict = {
            "query_embeddings": [q_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            query_kwargs["where"] = where_filter

        results = self._store._collection.query(**query_kwargs)

        documents = results["documents"][0]
        metadatas = results["metadatas"][0]

        if not documents:
            return RAGResponse(
                question=question,
                answer="No relevant chunks found in the database for your query.",
                sources=[],
                model=self._model,
                chunks_retrieved=0,
            )

        # 4. Build sources and context block
        sources: list[Source] = []
        context_parts: list[str] = []

        for i, (doc, meta) in enumerate(zip(documents, metadatas), start=1):
            sources.append(Source(
                ticker=meta["ticker"],
                filing_type=meta["filing_type"],
                filing_date=meta["filing_date"],
                section_path=meta.get("section_path", ""),
                relevance_rank=i,
            ))
            header = (
                f"[Source {i}] {meta['ticker']} {meta['filing_type']} "
                f"({meta['filing_date']}) — {meta.get('section_path', 'N/A')}"
            )
            context_parts.append(f"{header}\n{doc}")

        context_block = "\n\n".join(context_parts)
        user_message = f"{context_block}\n\nQuestion: {question}"

        # 5. Call the LLM
        logger.info(
            "Calling %s with %d chunks for question: %s",
            self._model, len(documents), question[:80],
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": user_message})

        response = self._llm.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.2,
        )

        answer = response.choices[0].message.content

        return RAGResponse(
            question=question,
            answer=answer,
            sources=sources,
            model=self._model,
            chunks_retrieved=len(documents),
        )

    @staticmethod
    def _build_where(
        ticker: str | None, filing_type: str | None
    ) -> dict | None:
        conditions = []
        if ticker:
            conditions.append({"ticker": {"$eq": ticker.upper()}})
        if filing_type:
            conditions.append({"filing_type": {"$eq": filing_type.upper()}})

        if len(conditions) == 2:
            return {"$and": conditions}
        if len(conditions) == 1:
            return conditions[0]
        return None
