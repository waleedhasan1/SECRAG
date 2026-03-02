"""Interactive CLI for querying SEC filings via RAG chain."""

import argparse
import sys

from dotenv import load_dotenv

from config import RAG_TOP_K
from rag_chain import RAGChain, RAGResponse
from vector_store import VectorStore


def format_response(resp: RAGResponse) -> str:
    """Format a RAGResponse for terminal display."""
    lines = [resp.answer, ""]

    if resp.sources:
        lines.append(f"Sources ({resp.chunks_retrieved} chunks retrieved):")
        for src in resp.sources:
            lines.append(
                f"  [{src.relevance_rank}] {src.ticker} {src.filing_type} "
                f"({src.filing_date}) — {src.section_path}"
            )
    lines.append(f"\nModel: {resp.model}")
    return "\n".join(lines)


def run_interactive(chain: RAGChain, ticker: str | None, filing_type: str | None):
    """Run interactive question loop."""
    print("SEC Filing RAG — Interactive Mode")
    print("Type your question, or 'quit'/'exit' to stop.\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        resp = chain.query(question, ticker=ticker, filing_type=filing_type)
        print(f"\n{format_response(resp)}\n")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Query SEC filings via RAG chain")
    parser.add_argument("question", nargs="?", help="Single-shot question (omit for interactive mode)")
    parser.add_argument("--ticker", help="Filter by company ticker (e.g. JPM)")
    parser.add_argument("--filing-type", help="Filter by filing type (e.g. 10-K)")
    parser.add_argument("--top-k", type=int, default=RAG_TOP_K, help="Number of chunks to retrieve")
    args = parser.parse_args()

    store = VectorStore()
    stats = store.get_stats()
    print(f"Connected to ChromaDB: {stats['total_chunks']} chunks in collection\n")

    if stats["total_chunks"] == 0:
        print("Error: No chunks in the vector store. Run main.py first to ingest filings.")
        sys.exit(1)

    chain = RAGChain(store)

    if args.question:
        # Single-shot mode
        resp = chain.query(
            args.question,
            ticker=args.ticker,
            filing_type=args.filing_type,
            top_k=args.top_k,
        )
        print(format_response(resp))
    else:
        # Interactive mode
        run_interactive(chain, ticker=args.ticker, filing_type=args.filing_type)


if __name__ == "__main__":
    main()
