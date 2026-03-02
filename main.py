"""CLI orchestrator for SEC filing data ingestion pipeline."""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from config import (
    CLEAN_DIR,
    FILING_TYPES,
    FILINGS_PER_TYPE,
    LOG_DIR,
    METADATA_INDEX_PATH,
    RAW_DIR,
    TARGET_COMPANIES,
)
from chunker import SECFilingChunker
from metadata_store import MetadataStore
from sec_downloader import EDGARDownloader
from sec_parser import SECFilingParser
from vector_store import VectorStore


def setup_logging():
    """Configure logging to console and file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "sec_downloader.log")

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def parse_args():
    parser = argparse.ArgumentParser(description="Download and parse SEC EDGAR filings")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Tickers to process (default: all target companies)",
    )
    parser.add_argument(
        "--filing-types",
        nargs="+",
        default=FILING_TYPES,
        help="Filing types to download (default: 10-K 10-Q)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading, only parse existing raw files",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Skip parsing, only download raw files",
    )
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="Skip chunking and embedding",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Delete existing chunks before re-embedding",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging()
    logger = logging.getLogger(__name__)

    # Determine which tickers to process
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
        invalid = [t for t in tickers if t not in TARGET_COMPANIES]
        if invalid:
            logger.error("Unknown tickers: %s", invalid)
            sys.exit(1)
    else:
        tickers = list(TARGET_COMPANIES.keys())

    downloader = EDGARDownloader()
    parser = SECFilingParser()
    store = MetadataStore(METADATA_INDEX_PATH)
    chunker = SECFilingChunker()
    vec_store = None
    if not args.skip_embed:
        try:
            vec_store = VectorStore()
        except Exception as e:
            logger.error("Vector store init failed: %s", e)
            logger.info("Continuing without embedding (use --skip-embed to suppress)")
            vec_store = None

    total_downloaded = 0
    total_parsed = 0
    total_embedded = 0
    errors = []

    for ticker in tickers:
        company_name, cik = TARGET_COMPANIES[ticker]
        logger.info("=" * 60)
        logger.info("Processing %s (%s, CIK %d)", ticker, company_name, cik)
        logger.info("=" * 60)

        try:
            # Fetch submissions and find filings
            submissions = downloader.get_company_filings(cik)
            filings = downloader.find_latest_filings(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                submissions=submissions,
                filing_types=args.filing_types,
                count_per_type=FILINGS_PER_TYPE,
            )

            if not filings:
                logger.warning("No filings found for %s", ticker)
                continue

            for meta in filings:
                raw_dir = os.path.join(RAW_DIR, ticker)
                clean_dir = os.path.join(CLEAN_DIR, ticker)

                # Download
                if not args.skip_download:
                    try:
                        downloader.download_filing(meta, raw_dir)
                        total_downloaded += 1
                    except Exception as e:
                        logger.error("Download failed for %s %s: %s",
                                     ticker, meta.filing_type, e)
                        errors.append(f"Download {ticker} {meta.filing_type}: {e}")
                        continue

                    # Detect wrapper 10-K and fetch companion document
                    try:
                        downloader.detect_and_fetch_companion(meta, meta.raw_path)
                    except Exception as e:
                        logger.warning(
                            "Companion detection failed for %s %s (non-fatal): %s",
                            ticker, meta.filing_type, e,
                        )

                # Parse
                if not args.skip_parse and meta.raw_path:
                    try:
                        safe_accession = meta.accession_number.replace("-", "_")
                        clean_filename = (
                            f"{ticker}_{meta.filing_type}_{meta.filing_date}"
                            f"_{safe_accession}.txt"
                        )
                        clean_path = os.path.join(clean_dir, clean_filename)

                        # Use companion document if available (wrapper filings)
                        parse_source = meta.companion_raw_path or meta.raw_path
                        parser.parse_file(parse_source, clean_path)
                        meta.clean_path = clean_path
                        total_parsed += 1

                        # Save metadata
                        store.save_sidecar(meta, clean_path)
                        store.update_index(meta)
                    except Exception as e:
                        logger.error("Parse failed for %s %s: %s",
                                     ticker, meta.filing_type, e)
                        errors.append(f"Parse {ticker} {meta.filing_type}: {e}")

                # Chunk and embed
                if vec_store and not args.skip_embed:
                    clean_path = meta.clean_path or os.path.join(
                        clean_dir,
                        f"{ticker}_{meta.filing_type}_{meta.filing_date}"
                        f"_{meta.accession_number.replace('-', '_')}.txt",
                    )
                    if os.path.exists(clean_path):
                        try:
                            if args.reindex:
                                vec_store.delete_filing(
                                    ticker, meta.filing_type, meta.filing_date
                                )

                            chunks = chunker.chunk_filing(
                                clean_path, ticker, company_name,
                                meta.filing_type, meta.filing_date,
                                meta.accession_number,
                            )
                            logger.info(
                                "Chunked %s %s: %d chunks (avg %d tokens)",
                                ticker, meta.filing_type, len(chunks),
                                sum(c.token_count for c in chunks) // max(len(chunks), 1),
                            )

                            added = vec_store.add_chunks(chunks)
                            total_embedded += added
                        except Exception as e:
                            logger.error(
                                "Embed failed for %s %s: %s",
                                ticker, meta.filing_type, e,
                            )
                            errors.append(f"Embed {ticker} {meta.filing_type}: {e}")

        except Exception as e:
            logger.error("Failed processing %s: %s", ticker, e)
            errors.append(f"Processing {ticker}: {e}")

    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("Tickers processed: %d", len(tickers))
    logger.info("Filings downloaded: %d", total_downloaded)
    logger.info("Filings parsed: %d", total_parsed)
    logger.info("Chunks embedded: %d", total_embedded)
    if vec_store:
        logger.info("Vector store: %s", vec_store.get_stats())
    if errors:
        logger.warning("Errors (%d):", len(errors))
        for err in errors:
            logger.warning("  - %s", err)
    else:
        logger.info("No errors.")


if __name__ == "__main__":
    main()
