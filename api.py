"""FastAPI backend for SEC filing RAG queries."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    SESSION_MAX_HISTORY,
    SESSION_TTL_MINUTES,
    TARGET_COMPANIES,
)
from rag_chain import RAGChain
from vector_store import VectorStore

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@dataclass
class Session:
    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    last_active: datetime = field(default_factory=datetime.utcnow)

    def add_turn(self, question: str, answer: str) -> None:
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        # Trim to max turns (each turn = 2 messages)
        max_messages = SESSION_MAX_HISTORY * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]
        self.last_active = datetime.utcnow()

    def is_expired(self) -> bool:
        return datetime.utcnow() - self.last_active > timedelta(minutes=SESSION_TTL_MINUTES)


def _clean_expired_sessions(sessions: dict[str, Session]) -> None:
    expired = [sid for sid, s in sessions.items() if s.is_expired()]
    for sid in expired:
        del sessions[sid]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str
    ticker: str | None = None
    filing_type: str | None = None
    session_id: str | None = None


class SourceOut(BaseModel):
    ticker: str
    filing_type: str
    filing_date: str
    section_path: str
    relevance_rank: int


class QueryResponse(BaseModel):
    session_id: str
    question: str
    answer: str
    sources: list[SourceOut]
    model: str
    chunks_retrieved: int


class CompanyOut(BaseModel):
    ticker: str
    name: str
    cik: int


class CompaniesResponse(BaseModel):
    companies: list[CompanyOut]


class HealthResponse(BaseModel):
    status: str
    total_chunks: int


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = VectorStore()
    chain = RAGChain(store)
    app.state.store = store
    app.state.chain = chain
    app.state.sessions: dict[str, Session] = {}
    stats = store.get_stats()
    logger.info("VectorStore ready — %d chunks", stats["total_chunks"])
    yield


app = FastAPI(
    title="SEC Filing RAG API",
    description="Query SEC filings using retrieval-augmented generation",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    stats = app.state.store.get_stats()
    return HealthResponse(status="ok", total_chunks=stats["total_chunks"])


@app.get("/companies", response_model=CompaniesResponse)
def companies():
    items = [
        CompanyOut(ticker=ticker, name=info[0], cik=info[1])
        for ticker, info in TARGET_COMPANIES.items()
    ]
    return CompaniesResponse(companies=items)


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    sessions: dict[str, Session] = app.state.sessions
    chain: RAGChain = app.state.chain

    # Lazy cleanup
    _clean_expired_sessions(sessions)

    # Resolve or create session
    if req.session_id:
        session = sessions.get(req.session_id)
        if session is None or session.is_expired():
            raise HTTPException(status_code=404, detail="Session not found or expired")
    else:
        session = Session(session_id=str(uuid.uuid4()))
        sessions[session.session_id] = session

    # Validate ticker
    if req.ticker and req.ticker.upper() not in TARGET_COMPANIES:
        valid = ", ".join(sorted(TARGET_COMPANIES.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ticker '{req.ticker}'. Valid tickers: {valid}",
        )

    # Run RAG query
    resp = chain.query(
        question=req.question,
        ticker=req.ticker,
        filing_type=req.filing_type,
        chat_history=session.history if session.history else None,
    )

    # Store turn (clean question only, not context-augmented)
    session.add_turn(req.question, resp.answer)

    sources = [
        SourceOut(
            ticker=s.ticker,
            filing_type=s.filing_type,
            filing_date=s.filing_date,
            section_path=s.section_path,
            relevance_rank=s.relevance_rank,
        )
        for s in resp.sources
    ]

    return QueryResponse(
        session_id=session.session_id,
        question=resp.question,
        answer=resp.answer,
        sources=sources,
        model=resp.model,
        chunks_retrieved=resp.chunks_retrieved,
    )


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=7999, reload=True)
