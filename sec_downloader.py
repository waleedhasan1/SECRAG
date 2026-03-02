"""EDGAR API client with rate limiting for downloading SEC filings."""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from config import SEC_USER_AGENT

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
WRAPPER_SIZE_THRESHOLD = 2_000_000  # 2MB — real 10-Ks are larger than this


@dataclass
class FilingMetadata:
    """Metadata for a single SEC filing."""

    ticker: str
    company_name: str
    cik: int
    filing_type: str
    filing_date: str
    accession_number: str
    primary_document: str
    filing_url: str
    raw_path: str = ""
    clean_path: str = ""
    companion_document: str = ""
    companion_raw_path: str = ""


class RateLimiter:
    """Enforces a maximum request rate (default 10 req/sec for SEC EDGAR)."""

    def __init__(self, max_per_second: float = 10.0):
        self._min_interval = 1.0 / max_per_second
        self._last_request = 0.0

    def wait(self):
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()


class EDGARDownloader:
    """Downloads SEC filings from EDGAR with rate limiting and retry logic."""

    def __init__(self, user_agent: str = SEC_USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self.rate_limiter = RateLimiter(max_per_second=10.0)

    def _get(self, url: str, max_retries: int = 3, timeout: int = 90) -> requests.Response:
        """Rate-limited GET with exponential backoff on 429/503."""
        for attempt in range(max_retries):
            self.rate_limiter.wait()
            try:
                resp = self.session.get(url, timeout=timeout)
            except requests.exceptions.Timeout:
                backoff = 5 * (2 ** attempt)
                logger.warning("Timeout on %s, retrying in %ds", url, backoff)
                time.sleep(backoff)
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (429, 503):
                backoff = 5 * (2 ** attempt)  # 5, 10, 20 seconds
                logger.warning("Got %d on %s, retrying in %ds", resp.status_code, url, backoff)
                time.sleep(backoff)
                continue

            resp.raise_for_status()

        raise RuntimeError(f"Failed to fetch {url} after {max_retries} retries")

    def get_company_filings(self, cik: int) -> dict:
        """Fetch the submissions JSON for a company from EDGAR."""
        cik_padded = str(cik).zfill(10)
        url = SUBMISSIONS_URL.format(cik=cik_padded)
        logger.info("Fetching submissions for CIK %s: %s", cik_padded, url)
        resp = self._get(url)
        return resp.json()

    def find_latest_filings(
        self,
        ticker: str,
        company_name: str,
        cik: int,
        submissions: dict,
        filing_types: list[str],
        count_per_type: int = 1,
    ) -> list[FilingMetadata]:
        """Extract the latest filings of each type from submissions data."""
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])

        results = []
        found_counts: dict[str, int] = {ft: 0 for ft in filing_types}

        for i, form in enumerate(forms):
            if form in filing_types and found_counts[form] < count_per_type:
                accession_no_dashes = accessions[i].replace("-", "")
                filing_url = ARCHIVES_URL.format(
                    cik=cik,
                    accession=accession_no_dashes,
                    document=documents[i],
                )
                meta = FilingMetadata(
                    ticker=ticker,
                    company_name=company_name,
                    cik=cik,
                    filing_type=form,
                    filing_date=dates[i],
                    accession_number=accessions[i],
                    primary_document=documents[i],
                    filing_url=filing_url,
                )
                results.append(meta)
                found_counts[form] += 1

            if all(c >= count_per_type for c in found_counts.values()):
                break

        return results

    def download_filing(self, metadata: FilingMetadata, output_dir: str) -> str:
        """Download the filing HTML to output_dir. Skips if file already exists."""
        os.makedirs(output_dir, exist_ok=True)

        safe_accession = metadata.accession_number.replace("-", "_")
        filename = f"{metadata.ticker}_{metadata.filing_type}_{metadata.filing_date}_{safe_accession}.html"
        filepath = os.path.join(output_dir, filename)

        if os.path.exists(filepath):
            logger.info("Already downloaded: %s", filepath)
            metadata.raw_path = filepath
            return filepath

        logger.info("Downloading %s %s (%s) -> %s",
                     metadata.ticker, metadata.filing_type, metadata.filing_date, filepath)
        resp = self._get(metadata.filing_url)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(resp.text)

        metadata.raw_path = filepath
        return filepath

    def detect_and_fetch_companion(
        self, meta: FilingMetadata, raw_path: str
    ) -> Optional[str]:
        """Detect wrapper 10-K filings and download the companion document.

        Wrapper filings (e.g. WFC, USB) are small HTML files that say
        "incorporated by reference" and point to a companion Annual Report
        HTM in the same EDGAR package. This method detects wrappers and
        downloads the real document so the parser gets actual financial data.

        Returns the companion file path, or None if not a wrapper.
        """
        # Only check 10-K filings
        if meta.filing_type != "10-K":
            return None

        # Skip if the raw file is large — it's a real 10-K, not a wrapper
        file_size = os.path.getsize(raw_path)
        if file_size > WRAPPER_SIZE_THRESHOLD:
            logger.debug(
                "%s 10-K is %d bytes (> %d), not a wrapper",
                meta.ticker, file_size, WRAPPER_SIZE_THRESHOLD,
            )
            return None

        # Read first 100KB and check for wrapper language
        with open(raw_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(100_000).lower()
        if "incorporated by reference" not in head:
            logger.debug("%s 10-K has no wrapper language, skipping companion check", meta.ticker)
            return None

        logger.info(
            "%s 10-K looks like a wrapper (%d bytes, has 'incorporated by reference'). "
            "Searching for companion document...",
            meta.ticker, file_size,
        )

        # Fetch the filing index to find all documents in the package
        accession_no_dashes = meta.accession_number.replace("-", "")
        index_url = FILING_INDEX_URL.format(cik=meta.cik, accession=accession_no_dashes)
        try:
            resp = self._get(index_url)
            index_data = resp.json()
        except Exception as e:
            logger.warning("Could not fetch filing index for %s: %s", meta.ticker, e)
            return None

        # Find the largest .htm file that isn't the primary document
        primary_name = meta.primary_document.lower()
        best_doc = None
        best_size = 0

        for item in index_data.get("directory", {}).get("item", []):
            name = item.get("name", "")
            size = int(item.get("size", 0) or 0)
            name_lower = name.lower()

            if name_lower == primary_name:
                continue
            if not name_lower.endswith((".htm", ".html")):
                continue
            if size > best_size:
                best_doc = name
                best_size = size

        if not best_doc:
            logger.info("No companion document found for %s", meta.ticker)
            return None

        if best_size <= file_size:
            logger.info(
                "Largest alternate HTM for %s is only %d bytes (primary is %d), skipping",
                meta.ticker, best_size, file_size,
            )
            return None

        # Download the companion
        companion_url = ARCHIVES_URL.format(
            cik=meta.cik, accession=accession_no_dashes, document=best_doc,
        )
        logger.info(
            "Downloading companion for %s: %s (%d bytes)",
            meta.ticker, best_doc, best_size,
        )

        try:
            resp = self._get(companion_url)
        except Exception as e:
            logger.warning("Failed to download companion for %s: %s", meta.ticker, e)
            return None

        # Save as {original_stem}_companion.html next to the primary file
        base, _ext = os.path.splitext(raw_path)
        companion_path = f"{base}_companion.html"
        with open(companion_path, "w", encoding="utf-8") as f:
            f.write(resp.text)

        meta.companion_document = best_doc
        meta.companion_raw_path = companion_path
        logger.info(
            "Saved companion for %s: %s (%d bytes)",
            meta.ticker, companion_path, best_size,
        )
        return companion_path
