"""
Nova — Academic & Document Agent
==================================

Persona: Professional, articulate, academically-minded.
Voice:   Female (en_US-amy-medium)

Capabilities:
- AWAY mode: Scans a configurable documents directory for new/modified
  PDFs, extracts text and metadata, and generates summaries for the
  briefing queue.
- ACTIVE mode: Answers user queries about documents, summarises PDFs,
  and sorts files by topic.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_logic.base_agent import BaseAgent
from core_logic.config import Config

logger = logging.getLogger(__name__)

# Configurable via env — where Nova looks for new documents
_DOCS_DIR = Path(
    os.getenv("NOVA_DOCS_DIR", str(Config.PROJECT_ROOT / "documents"))
)

_NOVA_PERSONALITY = """\
You are Nova, a professional academic assistant within the D.A.E.M.O.N. system.

Personality:
- Articulate, precise, and knowledgeable — like a brilliant teaching assistant
- Use clear, well-structured language suitable for academic contexts
- Reference document titles and page numbers when citing information
- Keep responses concise but thorough — no filler, no fluff

Expertise:
- PDF parsing and text extraction
- Document summarisation and key-point extraction
- Academic syllabus analysis and deadline tracking
- File organisation by topic, course, or date

When speaking aloud, use natural sentence flow — no bullet points or markdown.
"""


class NovaAgent(BaseAgent):
    """Academic & Document Agent — parses PDFs, extracts summaries."""

    @property
    def name(self) -> str:
        return "nova"

    @property
    def display_name(self) -> str:
        return "Nova"

    @property
    def description(self) -> str:
        return "Academic & document agent — PDF parsing, summarisation, file sorting"

    @property
    def voice_model(self) -> str:
        return os.getenv("NOVA_VOICE_MODEL", "en_US-amy-medium")

    @property
    def personality_prompt(self) -> str:
        return _NOVA_PERSONALITY

    @property
    def keywords(self) -> List[str]:
        return [
            "nova",
            "document", "pdf", "paper", "syllabus",
            "summarise", "summarize", "summary",
            "notes", "reading", "textbook",
            "assignment", "deadline", "course",
            "parse", "extract text",
        ]

    async def process_background_task(self) -> Dict[str, Any]:
        """Scan documents directory for new/modified PDFs.

        Returns a briefing-queue entry with a list of recently added
        or modified documents and (if PyPDF2 is available) their
        extracted metadata.
        """
        if not _DOCS_DIR.exists():
            return {}

        recent_files: List[Dict[str, Any]] = []
        cutoff = datetime.now(timezone.utc).timestamp() - 3600  # last hour

        try:
            for f in _DOCS_DIR.iterdir():
                if f.suffix.lower() == ".pdf" and f.stat().st_mtime > cutoff:
                    info: Dict[str, Any] = {
                        "filename": f.name,
                        "size_kb": round(f.stat().st_size / 1024, 1),
                        "modified": datetime.fromtimestamp(
                            f.stat().st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }

                    # Try to extract PDF metadata
                    try:
                        from PyPDF2 import PdfReader
                        reader = PdfReader(str(f))
                        meta = reader.metadata
                        info["pages"] = len(reader.pages)
                        if meta:
                            info["title"] = meta.get("/Title", "")
                            info["author"] = meta.get("/Author", "")
                    except ImportError:
                        info["note"] = "PyPDF2 not installed — metadata unavailable"
                    except Exception as exc:
                        info["parse_error"] = str(exc)

                    recent_files.append(info)

        except Exception as exc:
            logger.error(f"Nova background scan failed: {exc}")
            return {
                "agent": self.name,
                "category": "document_scan",
                "summary": f"Document scan failed: {exc}",
                "data": {"error": str(exc)},
            }

        if not recent_files:
            return {}

        summary = (
            f"Found {len(recent_files)} new or modified PDF(s) "
            f"in the documents folder."
        )
        if len(recent_files) <= 3:
            names = ", ".join(f["filename"] for f in recent_files)
            summary += f" Files: {names}."

        return {
            "agent": self.name,
            "category": "document_scan",
            "summary": summary,
            "data": {
                "docs_dir": str(_DOCS_DIR),
                "files": recent_files,
                "count": len(recent_files),
            },
        }

    async def generate_response(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Answer document-related queries.

        If an LLM is available in the context, uses it with the Nova
        personality prompt. Otherwise, returns a direct file-listing
        or extraction result.
        """
        # Try to use the LLM for a natural response
        llm = (context or {}).get("llm")
        if llm:
            try:
                response = llm.generate(
                    prompt=query,
                    system_prompt=self.personality_prompt,
                    temperature=0.5,
                    max_tokens=400,
                )
                return response
            except Exception as exc:
                logger.warning(f"Nova LLM generation failed: {exc}")

        # Fallback: direct response
        if not _DOCS_DIR.exists():
            return (
                "The documents directory doesn't exist yet. "
                "I'll be able to help once you add some PDFs there."
            )

        pdfs = list(_DOCS_DIR.glob("*.pdf"))
        if not pdfs:
            return "No PDFs found in the documents folder at the moment."

        names = ", ".join(f.name for f in pdfs[:5])
        extra = f" and {len(pdfs) - 5} more" if len(pdfs) > 5 else ""
        return (
            f"I can see {len(pdfs)} PDF(s) in the documents folder: "
            f"{names}{extra}. What would you like me to do with them?"
        )
