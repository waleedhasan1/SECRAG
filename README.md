# SEC Filing RAG Assistant

A full-stack Retrieval-Augmented Generation (RAG) system that lets users ask natural language questions about SEC filings (10-K and 10-Q) from major U.S. financial institutions. Built in five phases — from raw EDGAR downloads to a deployed chat interface.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js Frontend (Vercel)                                   │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │
│  │ Chat UI    │→ │ API Routes │→ │ /api/query, /companies │ │
│  └────────────┘  └────────────┘  └───────────┬────────────┘ │
└──────────────────────────────────────────────│───────────────┘
                                               │ HTTPS
┌──────────────────────────────────────────────│───────────────┐
│  FastAPI Backend                             ▼               │
│  ┌─────────┐  ┌──────────┐  ┌─────────────────────────────┐│
│  │ Session  │  │ RAGChain │→ │ ChromaDB + OpenAI Embeddings││
│  │ Manager  │  │ (GPT-4o) │  │ (5,260 chunks, cosine sim) ││
│  └─────────┘  └──────────┘  └─────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4 |
| API Proxy | Next.js Route Handlers (Vercel serverless) |
| Backend | FastAPI, Uvicorn |
| Vector DB | ChromaDB (persistent, cosine similarity) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) |
| Chat Model | OpenAI `gpt-4o-mini` (temp 0.2) |
| Tokenizer | `tiktoken` (cl100k_base) |
| Data Source | SEC EDGAR API |

## Target Companies

| Ticker | Company |
|--------|---------|
| JPM | JPMorgan Chase |
| GS | Goldman Sachs |
| BAC | Bank of America |
| C | Citigroup |
| MS | Morgan Stanley |
| WFC | Wells Fargo |
| USB | U.S. Bancorp |
| PNC | PNC Financial |
| STT | State Street |
| NTRS | Northern Trust |

Each company has one 10-K (annual) and one 10-Q (quarterly) filing indexed — 20 filings total, producing 5,260 chunks.

---

## Phase-by-Phase Development

### Phase 1 — SEC Filing Download & Parsing

**Goal:** Download 10-K and 10-Q filings from EDGAR for 10 banks, then convert raw HTML to clean plaintext.

**Files:** `sec_downloader.py`, `sec_parser.py`, `metadata_store.py`, `config.py`

**Tools used:**
- `requests` with session management for EDGAR API calls
- `BeautifulSoup` + `lxml` for HTML parsing
- Rate limiter enforcing SEC's 10 req/sec policy
- Exponential backoff (5s → 10s → 20s) on 429/503 errors

**How it works:**
1. Query EDGAR's `submissions` API for each company's CIK
2. Find the most recent 10-K and 10-Q filings
3. Download the primary HTML document from the filing package
4. Parse HTML through a 4-stage pipeline: remove scripts/styles → unwrap XBRL tags → extract structured text → post-process (normalize whitespace, strip page headers/footers, collapse blank lines)
5. Write `.txt` clean files and `.meta.json` sidecar metadata files

**Challenges & solutions:**

| Problem | Impact | Solution |
|---------|--------|----------|
| **Wrapper filings** — Wells Fargo's 10-K was only 91 KB of "incorporated by reference" boilerplate; the actual financials lived in a companion document (`wfc-20251231.htm`, 11.1 MB) | WFC data would have been nearly useless | Built wrapper detection: if a filing is < 2 MB and contains "incorporated by reference" language, scan the EDGAR package for the full companion document and download it automatically. WFC clean text jumped from 90 KB to 773 KB. |
| **EDGAR index empty sizes** — Some filing index entries had `size=""` instead of a number, crashing `int("")` | Pipeline crash on certain filings | Changed to `int(item.get("size", 0) or 0)` for safe coercion |
| **Blank line explosion** — 56.6% of all output lines were blank because the blank-line-collapsing regex ran *before* trailing whitespace was stripped | Noisy, bloated output files | Reordered post-processing: strip trailing whitespace first, then collapse `\n{3,}`. Reduced blank lines by 10.4 percentage points and total lines by 22.8%. |
| **Non-breaking spaces (`\xa0`)** — Up to 16,207 per file, interfering with tokenization | Bad token boundaries in Phase 2 | Added `\xa0` → space normalization as the first post-processing step |
| **Repeated "Table of Contents" headers** — SEC filings repeat this on every page (~250+ leftover lines) | Noise in chunks | Added regex to strip all TOC page-break headers, not just the first block |
| **Page numbers and headers** — Bare standalone page numbers (~3,900 lines) and company/form headers (~1,100 lines) survived initial stripping | Noise in chunks | Added comprehensive regex patterns for standalone page numbers, "Form 10-K" lines, and company-name-plus-page-number patterns |

**Result:** 20 filings across 10 banks, 12.1 MB of clean text, all metadata indexed.

---

### Phase 2 — Chunking & Embedding

**Goal:** Split clean text into semantically coherent, token-bounded chunks and embed them into a vector database.

**Files:** `chunker.py`, `vector_store.py`

**Tools used:**
- `tiktoken` (cl100k_base encoding) for accurate token counting
- `chromadb` PersistentClient with HNSW index (cosine distance)
- OpenAI `text-embedding-3-small` for 1536-dimensional vectors
- Deterministic chunk IDs: `{ticker}_{filing_type}_{date}_{index}_{md5}`

**How it works:**
1. **Section detection** (2-pass): identify Part/Item headers, Notes, and subsections to build a hierarchy tree
2. **Smart splitting**: respect paragraph boundaries → table blocks → sentence boundaries. Target 1,000 tokens per chunk (range 100–1,500) with 50-token overlap for context continuity.
3. **Tiny chunk merging**: chunks below 100 tokens are deferred and merged into the next chunk rather than discarded
4. **Batch embedding**: send chunks to OpenAI in batches of 20, with exponential backoff on rate limits
5. **Idempotent storage**: deterministic IDs mean re-runs skip already-embedded chunks

**Challenges & solutions:**

| Problem | Impact | Solution |
|---------|--------|----------|
| **OpenAI billing not configured** — Every embedding request returned `insufficient_quota` (HTTP 429) | Pipeline completely blocked | Set up billing on the OpenAI account. Total embedding cost for 5,260 chunks (~2.7M tokens): roughly $0.05. |
| **Rate limiting on Tier 1 account** — Initial batch size of 100 was too aggressive for a new account's rate limits | Slow pipeline with long waits between batches | Reduced batch size to 20. Tested with JPM (498 chunks) first, then ran the remaining 9 tickers. Pipeline's idempotency meant JPM was auto-skipped on the full run. Full embed completed in ~13 minutes. |
| **Query dimension mismatch** — Initial retrieval test used ChromaDB's built-in `query_texts()` which defaults to a 384-dim embedder, while stored vectors were 1536-dim | Queries returned irrelevant results | Switched to embedding the query with the same OpenAI embedder used for storage, then querying by vector |
| **Tiny cover-page chunks** — 20 chunks of < 100 tokens (just "UNITED STATES" from SEC cover pages) | Wasted embedding slots | Added tiny-chunk merging: chunks below min threshold are prepended to the next chunk |
| **Spurious cover-page sections** — Title-cased lines like "New York, New York" matched the subsection heuristic before the first Part I header | Noisy section hierarchy | Suppressed subsection detection for lines before the first Part/Item header; cover page is now grouped under a single "Preamble" section |

**Result:** 5,260 chunks stored in ChromaDB, 90% in the 100–1,000 token sweet spot, max 1,112 tokens.

---

### Phase 3 — RAG Chain & CLI

**Goal:** Build the retrieval-augmented generation pipeline that embeds a question, retrieves relevant chunks, and generates a cited answer.

**Files:** `rag_chain.py`, `query_cli.py`

**Tools used:**
- OpenAI Chat Completions API (`gpt-4o-mini`, temperature 0.2)
- ChromaDB vector similarity search with metadata filters
- System prompt engineering for citation behavior

**How it works:**
1. Embed the user's question with the same OpenAI embedder
2. Query ChromaDB for top-5 most similar chunks (optional ticker/filing_type filters)
3. Format a context block with numbered `[Source N]` labels and metadata
4. Send system prompt + chat history + context + question to GPT-4o-mini
5. Return the answer with a ranked source list

**System prompt design:** The LLM is instructed to answer *only* from the provided excerpts, cite sources as `[Source N]`, and politely refuse off-topic questions.

**Challenges & solutions:**

Phase 3 had minimal issues thanks to the solid foundation from Phases 1–2. The main design decisions were:
- **Clean history storage**: Chat history stores the user's raw question, not the context-augmented prompt. This prevents stale retrieval blocks from polluting future turns and saves tokens.
- **Temperature 0.2**: Low enough for factual accuracy, high enough to allow natural phrasing.

**Result:** End-to-end verified — JPMorgan risk factors returned 5 correctly cited sources, WFC net income with ticker filter returned WFC-only sources, off-topic questions were politely refused.

---

### Phase 4 — FastAPI Backend

**Goal:** Expose the RAG pipeline as a REST API with session management for multi-turn conversations.

**Files:** `api.py` (+ minor changes to `config.py`, `rag_chain.py`, `requirements.txt`)

**Tools used:**
- `FastAPI` with Pydantic request/response models
- `uvicorn` ASGI server
- In-memory session store with UUID-based session IDs
- CORS middleware (allow all origins for development)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status check + total chunk count |
| GET | `/companies` | List all 10 target companies |
| POST | `/query` | RAG query with optional session, ticker, and filing_type filters |

**Session management:**
- First query creates a new session (UUID) and returns the ID
- Follow-up queries pass the session ID to maintain conversation context
- Sessions expire after 60 minutes (lazy cleanup on each request)
- History capped at 20 turns (40 messages) to stay within token limits

**Challenges & solutions:**

| Problem | Impact | Solution |
|---------|--------|----------|
| **WSL2 localhost networking** — User couldn't access `localhost:8000` from their Windows browser because WSL2 runs on a separate virtual network | Couldn't test the API in browser | Identified the WSL2 IP address (`172.18.171.197`) for direct access. Also provided a PowerShell `netsh interface portproxy` command for permanent port forwarding. |
| **Session loss during development** — `uvicorn --reload` restarts the process on code changes, wiping the in-memory session store | Follow-up queries returned 404 after code edits | Expected development behavior. Sessions work correctly when the server isn't restarting. Production deployments use `reload=False`. |

**Result:** All 9 test scenarios passed — happy path queries, filtered queries, follow-up conversations, invalid ticker (400), expired session (404), missing field (422), and off-topic refusal (200 with polite decline).

---

### Phase 5 — Next.js Frontend

**Goal:** Build a chat interface with company/filing selectors, collapsible source citations, and Vercel-ready API proxy routes.

**Files:**
- `frontend/src/lib/api.ts` — TypeScript types + fetch wrappers
- `frontend/src/components/SourcesPanel.tsx` — collapsible `<details>/<summary>` sources list
- `frontend/src/components/ChatMessage.tsx` — user/assistant message bubbles
- `frontend/src/app/page.tsx` — full chat UI with state management
- `frontend/src/app/api/health/route.ts` — proxy to FastAPI `/health`
- `frontend/src/app/api/companies/route.ts` — proxy to FastAPI `/companies`
- `frontend/src/app/api/query/route.ts` — proxy to FastAPI `/query`

**Tools used:**
- Next.js 16 with App Router and Turbopack
- React 19 with hooks (`useState`, `useEffect`, `useRef`)
- Tailwind CSS 4 for styling
- Next.js Route Handlers as serverless API proxy

**UI layout:**
1. **Header** — app title + "New Chat" button
2. **Filter row** — company dropdown (10 banks + "All") and filing type dropdown (All / 10-K / 10-Q)
3. **Chat area** — scrollable message list with auto-scroll, "Thinking..." pulse animation while loading
4. **Error banner** — red bar for errors, auto-clears expired sessions
5. **Input bar** — text input with Enter-to-send + Send button

**API proxy pattern:** The frontend calls `/api/companies` and `/api/query` on its own Next.js server. These Route Handlers forward requests to the FastAPI backend using a server-side `API_URL` environment variable — no CORS needed, no backend URL exposed to the browser.

**Challenges & solutions:**

| Problem | Impact | Solution |
|---------|--------|----------|
| **Node.js version too old** — The system default was v18.12.1, which is too old for Next.js 15+ | `create-next-app` would fail | Switched to Node 22 via `nvm use 22` and created `.nvmrc` in the frontend directory |
| **Nested `.git` directory** — `create-next-app` initialized its own git repo inside `frontend/`, causing the parent repo to treat it as a submodule | `git add frontend/` silently skipped all files | Removed `frontend/.git` before committing |

**Result:** Build compiles with zero errors. Three API routes registered as dynamic serverless functions. Ready for Vercel deployment.

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 22+ (use `nvm use 22`)
- OpenAI API key with billing enabled

### 1. Backend Setup

```bash
# Clone and install
git clone git@github.com:waleedhasan1/SECRAG.git
cd SECRAG

# Create .env file
echo "OPENAI_API_KEY=sk-..." > .env

# Install Python dependencies
pip install -r requirements.txt

# Run the full data pipeline (download → parse → chunk → embed)
python main.py

# Start the API server
python api.py
# API available at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### 2. Frontend Setup

```bash
cd frontend
nvm use 22
npm install
npm run dev
# Open http://localhost:3000
```

### 3. Vercel Deployment

1. Push to GitHub
2. Import the `frontend/` directory in Vercel
3. Set environment variable: `API_URL` = your FastAPI backend's public URL
4. Deploy

---

## Project Structure

```
SECRAG/
├── config.py              # Centralized configuration (companies, paths, model params)
├── sec_downloader.py      # EDGAR API client with rate limiting & wrapper detection
├── sec_parser.py          # HTML → clean text (BeautifulSoup, XBRL handling)
├── chunker.py             # Semantic chunking with section hierarchy
├── metadata_store.py      # Filing metadata persistence (.meta.json + index)
├── vector_store.py        # ChromaDB + OpenAI embedding client
├── rag_chain.py           # Retrieval-augmented generation pipeline
├── query_cli.py           # Interactive CLI for queries
├── main.py                # Data ingestion orchestrator
├── api.py                 # FastAPI backend with session management
├── requirements.txt       # Python dependencies
├── .gitignore
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── api/
│   │   │   │   ├── health/route.ts    # GET proxy
│   │   │   │   ├── companies/route.ts # GET proxy
│   │   │   │   └── query/route.ts     # POST proxy
│   │   │   ├── page.tsx               # Chat UI
│   │   │   ├── layout.tsx             # App layout + metadata
│   │   │   └── globals.css            # Tailwind + scrollbar-hide
│   │   ├── components/
│   │   │   ├── ChatMessage.tsx        # Message bubbles
│   │   │   └── SourcesPanel.tsx       # Collapsible sources
│   │   └── lib/
│   │       └── api.ts                 # Types + fetch wrappers
│   ├── package.json
│   ├── next.config.ts
│   ├── tsconfig.json
│   └── .nvmrc
└── data/                  # (gitignored) raw filings, clean text, ChromaDB
```

---

## Key Design Decisions

**Idempotent pipeline** — Every stage can be re-run safely. The vector store uses deterministic chunk IDs and skips duplicates. Re-downloading skips existing files. This made recovery from rate limits and billing issues trivial.

**Semantic chunking over naive splitting** — Instead of splitting every N tokens, the chunker detects Part/Item/Note/subsection headers, keeps table blocks together, and respects paragraph boundaries. This produces chunks that map to meaningful document sections, improving retrieval relevance.

**Wrapper filing detection** — Some large financial institutions file a small "wrapper" document that references a companion filing. Without detecting this, Wells Fargo's 10-K would have been 90 KB of boilerplate instead of 773 KB of actual financials.

**Server-side API proxy** — The frontend never calls the FastAPI backend directly. Next.js Route Handlers proxy all requests server-side, keeping the backend URL private and eliminating CORS concerns. This also makes Vercel deployment straightforward.

**Low-temperature generation** — GPT-4o-mini at temperature 0.2 prioritizes factual accuracy over creative phrasing, appropriate for financial document Q&A.
