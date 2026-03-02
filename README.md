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

## How Everything Connects

The system is a pipeline where each stage feeds the next:

```
SEC EDGAR API
    │
    ▼
sec_downloader.py  ──→  data/raw/*.html       (raw filings)
    │
    ▼
sec_parser.py      ──→  data/clean/*.txt       (structured plaintext)
    │
    ▼
chunker.py         ──→  token-bounded chunks   (semantic sections)
    │
    ▼
vector_store.py    ──→  data/vectordb/         (ChromaDB + OpenAI embeddings)
    │
    ▼
rag_chain.py       ──→  question → retrieve → generate answer with citations
    │
    ▼
api.py             ──→  FastAPI REST endpoints + session management
    │
    ▼
frontend/          ──→  Next.js chat UI proxying to FastAPI
```

`config.py` is the central hub — every module imports paths, model parameters, and company definitions from it. Changing one value (like `CHUNK_TARGET_TOKENS` or `RAG_TOP_K`) propagates everywhere.

`main.py` orchestrates the full data pipeline: it calls the downloader, parser, chunker, and vector store in sequence for each company. On Railway, `start.sh` runs `main.py` on first deploy (when the volume is empty), then starts the API server.

## Tools & Why They Were Chosen

### Data Ingestion

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **requests** | HTTP client for EDGAR API | Lightweight, session support for connection pooling. EDGAR requires custom User-Agent headers and rate limiting (10 req/sec), which requests handles cleanly with session-level headers. |
| **BeautifulSoup + lxml** | HTML → plaintext parsing | SEC filings are deeply nested HTML with inline XBRL tags, inconsistent formatting, and embedded styles. BS4's tree traversal lets us surgically strip XBRL wrappers, scripts, and styles while preserving document structure. lxml is the fastest parser backend. |

### Chunking & Embedding

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **tiktoken** | Token counting | Uses the exact tokenizer (cl100k_base) that OpenAI's models use. Ensures chunk sizes actually respect model token limits, unlike character-based approximations. |
| **ChromaDB** | Vector database | Embedded (no separate server), persistent to disk, and supports metadata filtering. For 5,260 chunks this is the right scale — no need for Pinecone or Weaviate infrastructure. Cosine similarity with HNSW indexing gives fast retrieval. |
| **OpenAI text-embedding-3-small** | Vector embeddings (1536-dim) | Best cost/quality ratio for retrieval. At $0.02/1M tokens, embedding all 5,260 chunks (~2.7M tokens) costs about $0.05. Using the same embedder for both indexing and query ensures vector space alignment. |

### RAG & API

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **OpenAI gpt-4o-mini** | Answer generation | Fast, cheap, and strong at following citation instructions. Temperature 0.2 keeps answers factual — important for financial data. The system prompt constrains it to only cite provided excerpts. |
| **FastAPI** | REST API framework | Automatic OpenAPI/Swagger docs, Pydantic validation on every request/response, and async support. Type hints in Python map directly to typed API contracts. |
| **uvicorn** | ASGI server | Production-grade async server for FastAPI. On Railway, it runs as PID 1 (via `exec` in start.sh) so it receives shutdown signals properly. |

### Frontend & Deployment

| Tool | Purpose | Why this one |
|------|---------|-------------|
| **Next.js 16 (App Router)** | Frontend framework | Route Handlers let us proxy API calls server-side — the browser never sees the backend URL. This eliminates CORS issues and keeps the Railway URL private. Deployed on Vercel with zero config. |
| **React 19** | UI library | Hooks (`useState`, `useEffect`, `useRef`) handle chat state, auto-scroll, and input focus cleanly without class components or state management libraries. |
| **Tailwind CSS 4** | Styling | Utility classes keep all styling co-located with markup. No separate CSS files to maintain. The chat UI is ~200 lines of JSX with inline Tailwind — easy to read and modify. |
| **Railway** | Backend hosting | Dockerfile deployment with persistent volumes. The volume at `/app/data` survives redeploys, so the pipeline only runs once. Subsequent deploys start the API instantly. |
| **Vercel** | Frontend hosting | Native Next.js support. Route Handlers deploy as serverless functions automatically. Environment variables (`API_URL`) connect it to the Railway backend. |

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

## Key Design Decisions

**Idempotent pipeline** — Every stage can be re-run safely. The vector store uses deterministic chunk IDs (`{ticker}_{type}_{date}_{index}_{md5}`) and skips duplicates. This made recovery from rate limits and API errors trivial — just re-run `python main.py`.

**Semantic chunking over naive splitting** — Instead of splitting every N tokens, the chunker detects Part/Item/Note/subsection headers, keeps table blocks together, and respects paragraph boundaries. Chunks map to meaningful document sections, improving retrieval relevance. Target: 1,000 tokens per chunk (range 100–1,500) with 50-token overlap.

**Wrapper filing detection** — Some institutions file a small "wrapper" document that references a companion filing. The downloader detects these (< 2 MB with "incorporated by reference" language) and automatically fetches the companion document. Without this, Wells Fargo's 10-K would have been 90 KB of boilerplate instead of 773 KB of financials.

**Server-side API proxy** — The frontend never calls FastAPI directly. Next.js Route Handlers forward requests server-side, keeping the backend URL private and eliminating CORS concerns.

**First-deploy pipeline** — Railway's `start.sh` checks if the vector DB exists on the persistent volume. If empty, it runs the full pipeline (download → parse → chunk → embed), then starts uvicorn. After the first deploy, restarts skip the pipeline entirely.

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

### 3. Production Deployment

**Backend (Railway):**
1. Create a new Railway project from the GitHub repo
2. Add a persistent volume mounted at `/app/data`
3. Set environment variables: `OPENAI_API_KEY`, `DATA_DIR=/app/data`
4. First deploy runs the data pipeline automatically (~15 min)
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
