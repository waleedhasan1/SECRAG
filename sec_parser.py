"""HTML-to-text parser for SEC filings using BeautifulSoup + lxml."""

import html
import logging
import os
import re
import warnings

from bs4 import BeautifulSoup, Comment, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

# Tags that produce newlines when encountered
BLOCK_TAGS = {"div", "p", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "blockquote", "pre", "section", "article", "header", "footer"}

# Tags that produce tabs (table cells)
CELL_TAGS = {"td", "th"}

# Tags to remove entirely (with contents)
REMOVE_TAGS = {"script", "style", "ix:header", "ix:hidden"}

# XBRL inline tags to unwrap (keep inner text, remove the tag)
XBRL_UNWRAP_TAGS = {"ix:nonnumeric", "ix:nonfraction", "ix:continuation",
                     "ix:nonnumeric", "ix:nonfraction"}


class SECFilingParser:
    """4-stage pipeline to convert SEC filing HTML to clean text."""

    def parse_file(self, html_path: str, output_path: str) -> str:
        """Parse an HTML filing and write clean text to output_path."""
        logger.info("Parsing %s", html_path)

        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            raw_html = f.read()

        clean_text = self.parse_html(raw_html)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(clean_text)

        logger.info("Wrote clean text to %s (%d chars)", output_path, len(clean_text))
        return output_path

    def parse_html(self, raw_html: str) -> str:
        """Convert raw HTML to clean text through a 4-stage pipeline."""
        soup = BeautifulSoup(raw_html, "lxml")

        # Stage 1: Remove unwanted elements
        self._remove_unwanted(soup)

        # Stage 2: Unwrap XBRL tags
        self._unwrap_xbrl(soup)

        # Stage 3: Extract structured text
        text = self._extract_text(soup)

        # Stage 4: Post-process
        text = self._post_process(text)

        return text

    def _remove_unwanted(self, soup: BeautifulSoup):
        """Stage 1: Remove script, style, ix:header, ix:hidden, and HTML comments."""
        # Remove comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # Remove unwanted tags and their contents
        for tag_name in REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

    def _unwrap_xbrl(self, soup: BeautifulSoup):
        """Stage 2: Unwrap XBRL inline tags, keeping their inner text."""
        for tag_name in XBRL_UNWRAP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.unwrap()

    def _extract_text(self, soup: BeautifulSoup) -> str:
        """Stage 3: Walk the DOM and produce structured text."""
        parts = []
        self._walk(soup, parts)
        return "".join(parts)

    def _walk(self, element, parts: list):
        """Recursively walk the DOM, inserting newlines/tabs at block/cell boundaries."""
        from bs4 import NavigableString, Tag

        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child)
                # Collapse internal whitespace in inline text
                text = re.sub(r"[ \t]+", " ", text)
                parts.append(text)
            elif isinstance(child, Tag):
                tag_name = child.name.lower() if child.name else ""

                if tag_name in BLOCK_TAGS:
                    parts.append("\n")
                elif tag_name in CELL_TAGS:
                    parts.append("\t")

                self._walk(child, parts)

                if tag_name in BLOCK_TAGS:
                    parts.append("\n")

    def _post_process(self, text: str) -> str:
        """Stage 4: Clean up the extracted text."""
        # Unescape HTML entities
        text = html.unescape(text)

        # Normalize non-breaking spaces to regular spaces
        text = text.replace("\xa0", " ")

        # Collapse multiple spaces/tabs on the same line
        text = re.sub(r"[ \t]+", " ", text)

        # Strip trailing whitespace on each line (must come BEFORE blank line collapsing)
        text = re.sub(r" +\n", "\n", text)

        # Collapse multiple blank lines into at most two newlines
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Remove Table of Contents section (first large block)
        text = re.sub(
            r"(?i)\n\s*TABLE\s+OF\s+CONTENTS\s*\n.*?(?=\n\s*(?:PART|ITEM)\s+[IV\d])",
            "\n",
            text,
            count=1,
            flags=re.DOTALL,
        )

        # Remove repeated "Table of Contents" page-break headers
        text = re.sub(
            r"\n\s*Table\s+of\s+Contents\s*\n", "\n", text, flags=re.IGNORECASE
        )

        # Remove page numbers: "- 1 -", "Page 5", bare standalone numbers
        text = re.sub(r"\n\s*-\s*\d+\s*-\s*\n", "\n", text)
        text = re.sub(r"\n\s*Page\s+\d+\s*\n", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"\n\s*\d{1,3}\s*\n", "\n", text)

        # Remove common page headers/footers (e.g. "Company Name / 2025 Form 10-K")
        text = re.sub(
            r"\n\s*.{0,60}(?:Form|FORM)\s+10-[KkQq]\s*\n", "\n", text
        )

        # Final collapse in case removals created new blank runs
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip leading/trailing whitespace
        text = text.strip()

        return text
