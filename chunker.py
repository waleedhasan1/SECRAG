"""Section-aware semantic chunker for SEC filings."""

import re
from dataclasses import dataclass, field

import tiktoken

from config import (
    CHUNK_MAX_TOKENS,
    CHUNK_MIN_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
)


@dataclass
class ChunkMetadata:
    ticker: str
    company_name: str
    filing_type: str
    filing_date: str
    accession_number: str
    section_path: str
    chunk_index: int
    source_file: str


@dataclass
class Chunk:
    text: str
    metadata: ChunkMetadata
    token_count: int


@dataclass
class Section:
    title: str
    level: int
    start_line: int
    end_line: int = -1
    children: list = field(default_factory=list)
    parent: object = None

    @property
    def section_path(self) -> str:
        parts = []
        node = self
        while node is not None:
            parts.append(node.title)
            node = node.parent
        return " > ".join(reversed(parts))


# Compiled regexes for section detection
_PART_RE = re.compile(
    r"^(?:PART|Part)\s+(I{1,3}V?|IV)\b", re.IGNORECASE
)
_ITEM_RE = re.compile(
    r"^Item\s+(\d+[A-Z]?)\.?\s*(.*)", re.IGNORECASE
)
_NOTE_RE = re.compile(
    r"^(?:Note|NOTE)\s+(\d+)\s*[–\-—]\s*(.*)", re.IGNORECASE
)
_TABLE_LINE_RE = re.compile(r"[\$%]|\d{1,3}(?:,\d{3})+|\(\d+\)")


class SECFilingChunker:
    """Chunks SEC filing clean text into semantically coherent pieces."""

    def __init__(
        self,
        target_tokens: int = CHUNK_TARGET_TOKENS,
        max_tokens: int = CHUNK_MAX_TOKENS,
        min_tokens: int = CHUNK_MIN_TOKENS,
        overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    ):
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.overlap_tokens = overlap_tokens
        self._enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    # ------------------------------------------------------------------
    # Noise removal
    # ------------------------------------------------------------------

    def _remove_noise_lines(self, lines: list[str]) -> list[str]:
        """Remove repeated short lines (page headers/footers)."""
        line_counts: dict[str, int] = {}
        for line in lines:
            stripped = line.strip()
            if stripped:
                line_counts[stripped] = line_counts.get(stripped, 0) + 1

        noise = set()
        for text, count in line_counts.items():
            if (
                count >= 4
                and len(text) < 80
                and not _PART_RE.match(text)
                and not _ITEM_RE.match(text)
                and not _NOTE_RE.match(text)
            ):
                noise.add(text)

        return [l for l in lines if l.strip() not in noise]

    # ------------------------------------------------------------------
    # Section detection
    # ------------------------------------------------------------------

    def _detect_sections(self, lines: list[str]) -> list[Section]:
        """Detect section headers and build a flat list with levels.

        Two-pass approach: first scan for Part/Item headers to decide
        whether to suppress subsection detection in the cover page area.
        """
        # --- Pass 1: find the line of the first Part/Item header ---
        first_structural_line: int | None = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if _PART_RE.match(stripped) or _ITEM_RE.match(stripped):
                first_structural_line = i
                break

        has_part_item = first_structural_line is not None

        # --- Pass 2: full section detection ---
        sections: list[Section] = []
        added_preamble = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Part header (level 0)
            if _PART_RE.match(stripped):
                if not added_preamble and i > 0:
                    sections.append(Section(
                        title="Preamble", level=0, start_line=0))
                    added_preamble = True
                sections.append(Section(title=stripped, level=0, start_line=i))
                continue

            # Item header (level 1)
            m = _ITEM_RE.match(stripped)
            if m:
                if not added_preamble and i > 0:
                    sections.append(Section(
                        title="Preamble", level=0, start_line=0))
                    added_preamble = True
                # Combine Item number with description on same or next line
                title = stripped
                if not m.group(2).strip() and i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and len(next_line) < 120:
                        title = f"{stripped} {next_line}"
                sections.append(Section(title=title, level=1, start_line=i))
                continue

            # Note header (level 1)
            if _NOTE_RE.match(stripped):
                sections.append(Section(title=stripped, level=1, start_line=i))
                continue

            # Subsection: suppress before first Part/Item in structured docs
            if has_part_item and i < first_structural_line:
                continue

            # Subsection header: short, title-cased or all-caps, no terminal period
            words = stripped.split()
            numeric_words = sum(1 for w in words if w.replace(",", "").replace(".", "").isdigit())
            if (
                len(stripped) < 100
                and not stripped.endswith(".")
                and not stripped.endswith(",")
                and not stripped.endswith(";")
                and (stripped.istitle() or stripped.isupper())
                and not _TABLE_LINE_RE.search(stripped)
                and len(words) >= 2
                and len(words) <= 10
                and numeric_words < len(words) * 0.5
            ):
                sections.append(Section(title=stripped, level=2, start_line=i))

        # WFC fallback: when no Part/Item found (Exhibit 13), promote
        # free-form headers to higher levels
        if not has_part_item and sections:
            # Treat all detected headers as level-1 sections
            for s in sections:
                s.level = 1
            # Insert a synthetic root
            root = Section(title="Exhibit 13", level=0, start_line=0)
            sections.insert(0, root)

        return sections

    # ------------------------------------------------------------------
    # Hierarchy building
    # ------------------------------------------------------------------

    def _build_hierarchy(
        self, sections: list[Section], total_lines: int
    ) -> list[Section]:
        """Set end_line for each section and build parent-child tree.
        Returns the top-level sections."""
        if not sections:
            return [Section(title="Document", level=0, start_line=0,
                            end_line=total_lines)]

        # Set end_line for each section
        for i, sec in enumerate(sections):
            if i + 1 < len(sections):
                sec.end_line = sections[i + 1].start_line
            else:
                sec.end_line = total_lines

        # Build tree using a stack
        roots: list[Section] = []
        stack: list[Section] = []

        for sec in sections:
            # Pop stack until we find a parent with lower level
            while stack and stack[-1].level >= sec.level:
                stack.pop()

            if stack:
                sec.parent = stack[-1]
                stack[-1].children.append(sec)
            else:
                roots.append(sec)

            stack.append(sec)

        return roots

    def _collect_leaves(self, sections: list[Section]) -> list[Section]:
        """Collect leaf sections (no children) for chunking."""
        leaves = []
        for sec in sections:
            if not sec.children:
                leaves.append(sec)
            else:
                leaves.extend(self._collect_leaves(sec.children))
        return leaves

    # ------------------------------------------------------------------
    # Table detection
    # ------------------------------------------------------------------

    def _is_table_line(self, line: str) -> bool:
        """Heuristic: line is part of a table if it has numeric/currency patterns."""
        stripped = line.strip()
        if not stripped:
            return False
        return bool(_TABLE_LINE_RE.search(stripped))

    # ------------------------------------------------------------------
    # Chunk splitting
    # ------------------------------------------------------------------

    def _split_section_text(self, text: str) -> list[str]:
        """Split section text into chunks respecting paragraph and table boundaries."""
        total_tokens = self.count_tokens(text)

        if total_tokens <= self.target_tokens:
            return [text]

        # Split into paragraphs on double newlines
        paragraphs = re.split(r"\n\n+", text)
        # Merge table blocks: consecutive lines with table patterns
        merged_blocks = self._merge_table_blocks(paragraphs)

        chunks = []
        current_parts: list[str] = []
        current_tokens = 0

        for block in merged_blocks:
            block_tokens = self.count_tokens(block)

            # Single block exceeds max — split at sentence boundaries
            if block_tokens > self.max_tokens:
                # Flush current
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_tokens = 0

                sentence_chunks = self._split_at_sentences(block)
                chunks.extend(sentence_chunks)
                continue

            # Adding this block would exceed target — flush
            if current_tokens + block_tokens > self.target_tokens and current_parts:
                chunks.append("\n\n".join(current_parts))
                # Overlap: carry last block forward
                if self.overlap_tokens > 0 and current_parts:
                    last = current_parts[-1]
                    last_tokens = self.count_tokens(last)
                    if last_tokens <= self.overlap_tokens:
                        current_parts = [last]
                        current_tokens = last_tokens
                    else:
                        current_parts = []
                        current_tokens = 0
                else:
                    current_parts = []
                    current_tokens = 0

            current_parts.append(block)
            current_tokens += block_tokens

        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return chunks

    def _merge_table_blocks(self, paragraphs: list[str]) -> list[str]:
        """Keep consecutive table paragraphs together."""
        merged = []
        table_buffer: list[str] = []

        for para in paragraphs:
            lines = para.split("\n")
            table_lines = sum(1 for l in lines if self._is_table_line(l))
            is_table = table_lines > len(lines) * 0.5 and len(lines) >= 2

            if is_table:
                table_buffer.append(para)
            else:
                if table_buffer:
                    merged.append("\n\n".join(table_buffer))
                    table_buffer = []
                merged.append(para)

        if table_buffer:
            merged.append("\n\n".join(table_buffer))

        return merged

    def _split_at_sentences(self, text: str) -> list[str]:
        """Split text at sentence boundaries when a single paragraph is too long."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks = []
        current_parts: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            s_tokens = self.count_tokens(sentence)
            if current_tokens + s_tokens > self.target_tokens and current_parts:
                chunks.append(" ".join(current_parts))
                current_parts = []
                current_tokens = 0
            current_parts.append(sentence)
            current_tokens += s_tokens

        if current_parts:
            chunks.append(" ".join(current_parts))

        return chunks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_filing(
        self,
        clean_text_path: str,
        ticker: str,
        company_name: str,
        filing_type: str,
        filing_date: str,
        accession_number: str,
    ) -> list[Chunk]:
        """Chunk a single clean-text filing into semantically coherent pieces."""
        with open(clean_text_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        lines = raw_text.split("\n")

        # Step 1: Remove noise lines
        lines = self._remove_noise_lines(lines)

        # Step 2: Detect sections
        section_list = self._detect_sections(lines)

        # Step 3: Build hierarchy
        roots = self._build_hierarchy(section_list, len(lines))

        # Step 4: Collect leaf sections and chunk
        leaves = self._collect_leaves(roots)

        # If no sections found at all, treat whole document as one section
        if not leaves:
            leaves = [Section(title="Document", level=0, start_line=0,
                              end_line=len(lines))]

        chunks: list[Chunk] = []
        chunk_index = 0
        # Buffer for merging tiny chunks into the next one
        pending_text: str | None = None
        pending_section_path: str | None = None

        for leaf in leaves:
            section_text = "\n".join(
                lines[leaf.start_line : leaf.end_line]
            ).strip()

            if not section_text:
                continue

            text_chunks = self._split_section_text(section_text)

            for text in text_chunks:
                # Merge pending tiny chunk into this one
                if pending_text is not None:
                    text = pending_text + "\n\n" + text
                    pending_text = None
                    pending_section_path = None

                token_count = self.count_tokens(text)

                # Defer tiny chunks — merge into the next chunk
                if token_count < self.min_tokens:
                    pending_text = text
                    pending_section_path = leaf.section_path
                    continue

                metadata = ChunkMetadata(
                    ticker=ticker,
                    company_name=company_name,
                    filing_type=filing_type,
                    filing_date=filing_date,
                    accession_number=accession_number,
                    section_path=leaf.section_path,
                    chunk_index=chunk_index,
                    source_file=clean_text_path,
                )
                chunks.append(Chunk(
                    text=text, metadata=metadata, token_count=token_count
                ))
                chunk_index += 1

        # If a tiny chunk remains at the very end, append to last chunk or emit it
        if pending_text is not None:
            if chunks:
                last = chunks[-1]
                merged = last.text + "\n\n" + pending_text
                chunks[-1] = Chunk(
                    text=merged,
                    metadata=last.metadata,
                    token_count=self.count_tokens(merged),
                )
            else:
                metadata = ChunkMetadata(
                    ticker=ticker,
                    company_name=company_name,
                    filing_type=filing_type,
                    filing_date=filing_date,
                    accession_number=accession_number,
                    section_path=pending_section_path or "Document",
                    chunk_index=0,
                    source_file=clean_text_path,
                )
                chunks.append(Chunk(
                    text=pending_text, metadata=metadata,
                    token_count=self.count_tokens(pending_text),
                ))

        return chunks
