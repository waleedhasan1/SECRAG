"""Metadata sidecar and master index management for SEC filings."""

import json
import logging
import os
from dataclasses import asdict

from sec_downloader import FilingMetadata

logger = logging.getLogger(__name__)


class MetadataStore:
    """Manages per-filing .meta.json sidecars and a master metadata index."""

    def __init__(self, index_path: str):
        self.index_path = index_path

    def save_sidecar(self, metadata: FilingMetadata, clean_path: str) -> str:
        """Write a .meta.json sidecar file next to the clean text file."""
        sidecar_path = clean_path.replace(".txt", ".meta.json")
        data = asdict(metadata)
        data["clean_path"] = clean_path

        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("Saved sidecar: %s", sidecar_path)
        return sidecar_path

    def update_index(self, metadata: FilingMetadata):
        """Add or update an entry in the master metadata index."""
        index = self._load_index()

        key = f"{metadata.ticker}_{metadata.filing_type}_{metadata.filing_date}"
        index[key] = asdict(metadata)

        self._save_index(index)
        logger.info("Updated index entry: %s", key)

    def _load_index(self) -> dict:
        """Load the master index from disk, or return empty dict if not found."""
        if os.path.exists(self.index_path):
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self, index: dict):
        """Write the master index to disk."""
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
