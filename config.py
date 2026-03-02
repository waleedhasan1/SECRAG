"""Configuration constants for SEC EDGAR filing downloader."""

import os

# SEC EDGAR requires a User-Agent identifying the requester
SEC_USER_AGENT = "Waleed Hasan waleed.1.hasan@gmail.com"

# Target companies: ticker -> (company_name, CIK)
TARGET_COMPANIES = {
    "NTRS": ("Northern Trust Corp", 73124),
    "JPM": ("JPMorgan Chase & Co", 19617),
    "GS": ("Goldman Sachs Group Inc", 886982),
    "BAC": ("Bank of America Corp", 70858),
    "C": ("Citigroup Inc", 831001),
    "MS": ("Morgan Stanley", 895421),
    "WFC": ("Wells Fargo & Company", 72971),
    "USB": ("US Bancorp", 36104),
    "PNC": ("PNC Financial Services Group", 713676),
    "STT": ("State Street Corp", 93751),
}

# Filing types to download and count per company
FILING_TYPES = ["10-K", "10-Q"]
FILINGS_PER_TYPE = 1

# Project paths
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
CLEAN_DIR = os.path.join(DATA_DIR, "clean")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
METADATA_INDEX_PATH = os.path.join(DATA_DIR, "metadata_index.json")

# Phase 2: Chunking & Embedding
VECTORDB_DIR = os.path.join(DATA_DIR, "vectordb")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_BATCH_SIZE = 20
CHUNK_TARGET_TOKENS = 1000
CHUNK_MAX_TOKENS = 1500
CHUNK_MIN_TOKENS = 100
CHUNK_OVERLAP_TOKENS = 50
CHROMA_COLLECTION_NAME = "sec_filings"

# Phase 3: RAG Retrieval Chain
OPENAI_CHAT_MODEL = "gpt-4o-mini"
RAG_TOP_K = 5
RAG_MAX_CONTEXT_TOKENS = 6000

# Phase 4: API / Session Management
SESSION_TTL_MINUTES = 60
SESSION_MAX_HISTORY = 20
