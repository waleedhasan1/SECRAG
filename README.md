# SEC Filing RAG Assistant

A full-stack Retrieval-Augmented Generation (RAG) system that lets users ask natural language questions about SEC filings (10-K and 10-Q) from major U.S. financial institutions. The system downloads filings from EDGAR, parses and chunks them semantically, embeds them into a vector database, and serves answers through a chat interface with cited sources.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Next.js Frontend (Vercel)                                      │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Chat UI    │→ │ Route        │→ │ /api/query, /companies │  │
│  │ (React 19) │  │ Handlers     │  │ (serverless proxy)     │  │
│  └────────────┘  └──────────────┘  └───────────┬────────────┘  │
└────────────────────────────────────────────────│────────────────┘
                                                 │ HTTPS
┌────────────────────────────────────────────────│────────────────┐
│  FastAPI Backend (Railway)                     ▼                │
│  ┌───────────┐  ┌────────────┐  ┌────────────────────────────┐ │
│  │ Session    │  │ RAG Chain  │→ │ ChromaDB + OpenAI          │ │
│  │ Manager    │  │ (GPT-4o-  │  │ Embeddings (5,260 chunks,  │ │
│  │ (in-mem)   │  │  mini)     │  │ cosine similarity)         │ │
│  └───────────┘  └────────────┘  └────────────────────────────┘ │
│                                         ▲                       │
│  ┌──────────────────────────────────────┘                      │
│  │  Persistent Volume (/app/data)                               │
│  │  ├── raw/      (EDGAR HTML downloads)                        │
│  │  ├── clean/    (parsed plaintext)                            │
│  │  └── vectordb/ (ChromaDB storage)                            │
│  └──────────────────────────────────────────────────────────────│
└─────────────────────────────────────────────────────────────────┘
```

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

## Phase 1 — Data Collection

**Goal:** Gather raw SEC filings from EDGAR and transform them into clean, structured plaintext ready for machine processing.

**Files:** `sec_downloader.py`, `sec_parser.py`, `metadata_store.py`, `config.py`, `main.py`

### How it works

The pipeline starts at the SEC's EDGAR API. For each of the 10 target companies, `sec_downloader.py` queries the EDGAR submissions endpoint using the company's CIK number, identifies the most recent 10-K and 10-Q filings, and downloads the primary HTML document from each filing package.

The raw HTML then flows into `sec_parser.py`, which runs a 4-stage cleaning pipeline: strip scripts and styles, unwrap inline XBRL tags (a financial markup format the SEC requires), extract structured text preserving section hierarchy, and post-process to normalize whitespace, remove repeated "Table of Contents" headers, and strip page numbers.

Each filing gets a `.meta.json` sidecar file containing the ticker, filing type, date, accession number, and file paths. `metadata_store.py` maintains a central index so downstream stages know exactly what data is available.

`main.py` orchestrates the full pipeline — it loops through all companies and calls the downloader, parser, chunker, and vector store in sequence. On Railway, `start.sh` runs `main.py` on first deploy when the persistent volume is empty.

### Tools used

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **requests** | HTTP client for EDGAR API | Session support for connection pooling and persistent headers. EDGAR requires a custom User-Agent identifying the requester and enforces a 10 requests/second rate limit — `requests.Session` handles both cleanly with session-level headers and a built-in rate limiter with exponential backoff. |
| **BeautifulSoup + lxml** | HTML → plaintext parsing | SEC filings are deeply nested HTML with inline XBRL tags, inconsistent formatting, and embedded styles. BS4's tree traversal lets us surgically strip XBRL wrappers, scripts, and styles while preserving the document's section structure. lxml was chosen as the parser backend for speed. |
| **python-dotenv** | Environment variable loading | Loads `OPENAI_API_KEY` from a `.env` file in development so credentials stay out of source code. |

### What connects to what

```
config.py  ──→  TARGET_COMPANIES (tickers, CIKs), file paths, rate limit settings
    │
    ▼
sec_downloader.py  ──→  EDGAR API  ──→  data/raw/{ticker}/*.html
    │
    ▼
sec_parser.py  ──→  data/clean/{ticker}/*.txt  +  *.meta.json
    │
    ▼
metadata_store.py  ──→  data/metadata_index.json  (central filing registry)
```

`config.py` is the central hub for the entire project — every module imports company definitions, directory paths, and model parameters from it. Changing a value in `config.py` propagates everywhere.

### Result

20 filings across 10 banks, producing 12.1 MB of clean structured plaintext, all metadata indexed and ready for chunking.

---

## Phase 2 — Data Processing & Storage

**Goal:** Split the clean text into semantically coherent, token-bounded chunks, embed them as vectors, and store them in a database that supports fast similarity search. Then expose the data through a REST API with a retrieval-augmented generation pipeline.

**Files:** `chunker.py`, `vector_store.py`, `rag_chain.py`, `api.py`

### Chunking

`chunker.py` takes the clean `.txt` files and splits them into chunks that are meaningful, not arbitrary. Instead of cutting every N tokens, it makes two passes over the document:

1. **Section detection**: Identifies Part headers (Part I, Part II), Item headers (Item 1, Item 7), Notes to Financial Statements, and subsection headings. This builds a hierarchy tree so each chunk knows its position in the document (e.g., "Part II > Item 7 > Revenue Recognition").

2. **Smart splitting**: Within each section, it respects natural boundaries — paragraph breaks first, then table blocks (kept whole so financial tables aren't split mid-row), then sentence boundaries as a last resort. Target size is 1,000 tokens per chunk (range 100–1,500) with 50-token overlap between adjacent chunks for context continuity. Chunks below 100 tokens get merged into the next chunk rather than stored as fragments.

### Embedding & Vector Storage

`vector_store.py` takes the chunks and makes them searchable. Each chunk's text is sent to OpenAI's embedding API, which returns a 1536-dimensional vector representing the chunk's semantic meaning. These vectors are stored in ChromaDB alongside the original text and metadata (ticker, filing type, date, section path).

At query time, the user's question gets embedded with the same model, and ChromaDB finds the chunks whose vectors are closest (cosine similarity). This means "What are JPMorgan's risk factors?" matches chunks from JPM's Risk Factors section even if they don't contain those exact words.

Chunk IDs are deterministic (`{ticker}_{type}_{date}_{index}_{md5}`), so re-running the pipeline skips already-embedded chunks. The entire pipeline is idempotent.

### RAG Chain

`rag_chain.py` ties retrieval to generation. When a question comes in:

1. Embed the question with the same OpenAI embedder used for storage
2. Query ChromaDB for the top 5 most similar chunks (with optional ticker/filing type filters)
3. Format the chunks as numbered `[Source N]` excerpts with metadata headers
4. Send the system prompt + conversation history + context + question to GPT-4o-mini
5. Return the answer with a ranked source list

The system prompt instructs the model to answer only from the provided excerpts, cite sources using `[Source N]` notation, and decline off-topic questions. Temperature is set to 0.2 — low enough for factual accuracy on financial data, high enough for natural phrasing.

### FastAPI Backend

`api.py` wraps the RAG chain in a REST API with three endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status check + total chunk count |
| GET | `/companies` | List all 10 target companies |
| POST | `/query` | RAG query with optional session, ticker, and filing type filters |

Session management handles multi-turn conversations: the first query creates a session (UUID), follow-up queries pass the session ID to maintain context. Sessions expire after 60 minutes, and history is capped at 20 turns to stay within token limits. Chat history stores the user's raw question, not the context-augmented prompt — this prevents stale retrieval blocks from polluting future turns.

### Tools used

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **tiktoken** | Token counting | Uses the exact tokenizer (cl100k_base) that OpenAI's embedding and chat models use. Ensures chunk sizes actually respect model token limits, unlike character-based approximations. |
| **ChromaDB** | Vector database | Embedded (no separate server process), persistent to disk, and supports metadata filtering on queries. For 5,260 chunks this is the right scale — no need for managed services like Pinecone or Weaviate. Uses HNSW indexing with cosine distance for fast approximate nearest-neighbor search. |
| **OpenAI text-embedding-3-small** | Vector embeddings (1536-dim) | Best cost/quality ratio for retrieval tasks. At $0.02/1M tokens, embedding all 5,260 chunks (~2.7M tokens) costs about $0.05. Using the same model for both indexing and querying ensures the vectors live in the same semantic space. |
| **OpenAI gpt-4o-mini** | Answer generation | Fast, cheap, and strong at following structured instructions (citation format, source-only answers). Temperature 0.2 keeps it factual. The system prompt constrains it to only reference provided excerpts. |
| **FastAPI** | REST API framework | Automatic OpenAPI/Swagger docs from Python type hints, Pydantic validation on every request and response, and native async support. Type annotations in the Python code map directly to a typed API contract that the frontend can rely on. |
| **uvicorn** | ASGI server | Production-grade async server purpose-built for FastAPI. On Railway, it runs as PID 1 via `exec` in the startup script so it receives shutdown signals properly. |

### What connects to what

```
chunker.py  ──→  reads data/clean/*.txt  ──→  produces Chunk objects with metadata
    │
    ▼
vector_store.py  ──→  OpenAI Embeddings API  ──→  ChromaDB (data/vectordb/)
    │
    ▼
rag_chain.py  ──→  embeds question  ──→  queries ChromaDB  ──→  GPT-4o-mini  ──→  cited answer
    │
    ▼
api.py  ──→  /health, /companies, /query  ──→  session management (in-memory)
```

### Result

5,260 chunks stored in ChromaDB (90% in the 100–1,000 token range), a RAG pipeline that retrieves and cites relevant sources, and a REST API serving it all with session-based multi-turn conversations.

---

## Phase 3 — Presentation & Deployment

**Goal:** Build a chat interface for end users, deploy the backend as an always-on service, and connect everything so the app works publicly as a portfolio piece.

**Files:** `frontend/src/**/*`, `Dockerfile`, `start.sh`, `railway.toml`, `.dockerignore`

### Next.js Frontend

The frontend is a single-page chat application built with Next.js and React. The UI has four sections:

1. **Header** — app title + "New Chat" button to reset the conversation
2. **Filter row** — company dropdown (10 banks + "All") and filing type selector (All / 10-K / 10-Q)
3. **Chat area** — scrollable message list with user/assistant bubbles, auto-scroll on new messages, a "Thinking..." animation while waiting, and collapsible source citations on each response
4. **Input bar** — text input with Enter-to-send and a Send button

When no conversation is active, the app displays example questions users can click to get started, along with a note about what data is covered.

The critical architectural decision is the **server-side API proxy**. The browser never calls the FastAPI backend directly. Instead, Next.js Route Handlers (`/api/query`, `/api/companies`, `/api/health`) forward requests server-side to the backend using a private `API_URL` environment variable. This eliminates CORS issues entirely and keeps the backend URL hidden from the browser.

### Railway Deployment (Backend)

The backend runs on Railway inside a Docker container. The deployment setup:

- **Dockerfile**: Python 3.11-slim base, installs pip dependencies (cached layer), copies the Python source files and startup script
- **start.sh**: Checks if `/app/data/vectordb` exists on the persistent volume. If empty (first deploy), runs the full data pipeline via `python main.py`. Then starts uvicorn. Uses `exec` so uvicorn becomes PID 1 and handles signals correctly.
- **railway.toml**: Configures the Dockerfile builder and restart policy
- **Persistent volume**: Mounted at `/app/data`, survives across redeploys. The pipeline only runs once — every subsequent deploy starts the API instantly.

`config.py` reads `DATA_DIR` from an environment variable (falling back to `./data` locally), so Railway's volume mount at `/app/data` works without code changes.

### Vercel Deployment (Frontend)

The Next.js frontend deploys on Vercel with zero configuration. The Route Handlers become serverless functions automatically. The only setup is setting the `API_URL` environment variable to the Railway backend's public URL.

The data flow in production:

```
User's browser
    │
    ▼
Vercel (Next.js)  ──→  server-side fetch to API_URL
    │
    ▼
Railway (FastAPI)  ──→  ChromaDB lookup  ──→  OpenAI GPT-4o-mini  ──→  response
    │
    ▼
Vercel  ──→  renders response in chat UI  ──→  browser
```

### Tools used

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **Next.js 16 (App Router)** | Frontend framework | Route Handlers enable the server-side API proxy pattern — the browser never sees the backend URL. App Router with React Server Components is the modern Next.js architecture, and Vercel deploys it natively. |
| **React 19** | UI library | Hooks (`useState`, `useEffect`, `useRef`) manage chat state, auto-scroll, and input focus without external state management libraries. The entire chat UI is a single component with clear, linear state flow. |
| **Tailwind CSS 4** | Styling | Utility classes keep styling co-located with markup — no separate CSS files to maintain. The full chat UI is ~200 lines of JSX with inline Tailwind, making it easy to read and modify. |
| **Docker** | Containerization | Packages the Python backend with all its dependencies into a reproducible image. The Dockerfile uses layer caching (dependencies installed before code copy) so rebuilds after code changes take seconds. |
| **Railway** | Backend hosting | Supports Dockerfile deployment with persistent volumes — essential for ChromaDB's on-disk storage. The volume at `/app/data` means the expensive data pipeline runs once and persists forever. |
| **Vercel** | Frontend hosting | Native Next.js support with automatic serverless function deployment for Route Handlers. Environment variables connect it to the Railway backend with a single config change. |

### Result

A publicly accessible chat application where users can ask questions about SEC filings from 10 major financial institutions, get cited answers sourced from real EDGAR data, and filter by company or filing type — all running as an always-on service suitable for a portfolio.

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 22+ (use `nvm use 22`)
- OpenAI API key with billing enabled

### Local Development

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

```bash
# In a separate terminal
cd frontend
nvm use 22
npm install
npm run dev
# Open http://localhost:3000
```

### Production Deployment

**Backend (Railway):**
1. Create a new Railway project from the GitHub repo
2. Add a persistent volume mounted at `/app/data`
3. Set environment variables: `OPENAI_API_KEY`, `DATA_DIR=/app/data`
4. First deploy runs the data pipeline automatically
5. Copy the Railway public URL

**Frontend (Vercel):**
1. Import the `frontend/` directory in Vercel
2. Set environment variable: `API_URL` = Railway public URL
3. Deploy

## Project Structure

```
SECRAG/
├── config.py              # Central config (companies, paths, model params)
├── sec_downloader.py      # EDGAR API client with rate limiting
├── sec_parser.py          # HTML → clean text (BeautifulSoup + lxml)
├── chunker.py             # Semantic chunking with section hierarchy
├── metadata_store.py      # Filing metadata persistence
├── vector_store.py        # ChromaDB + OpenAI embedding client
├── rag_chain.py           # Retrieval-augmented generation pipeline
├── query_cli.py           # Interactive CLI for queries
├── main.py                # Data ingestion orchestrator
├── api.py                 # FastAPI backend with session management
├── requirements.txt       # Python dependencies
├── Dockerfile             # Railway deployment image
├── start.sh               # Startup script (pipeline check + uvicorn)
├── railway.toml           # Railway build/deploy config
├── .dockerignore
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── api/
│   │   │   │   ├── health/route.ts
│   │   │   │   ├── companies/route.ts
│   │   │   │   └── query/route.ts
│   │   │   ├── page.tsx              # Chat UI
│   │   │   ├── layout.tsx
│   │   │   └── globals.css
│   │   ├── components/
│   │   │   ├── ChatMessage.tsx
│   │   │   └── SourcesPanel.tsx
│   │   └── lib/
│   │       └── api.ts
│   ├── package.json
│   └── .nvmrc
└── data/                  # (gitignored) raw filings, clean text, ChromaDB
```
