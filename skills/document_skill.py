"""
Document Processing Skill

Handle PDF reading, text extraction, and document operations.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class DocumentSkill:
    """Handle document processing queries."""
    
    SUPPORTED_FORMATS = ['.pdf', '.txt', '.md']
    
    @staticmethod
    def _get_project_root() -> Path:
        """Get project root directory."""
        return Path(__file__).parent.parent
    
    @staticmethod
    def _safe_path(file_path: str) -> Optional[Path]:
        """
        Resolve a user-provided file path.

        Behaviour:
            * Strips quotes / surrounding whitespace.
            * If absolute, returned as-is when ALLOW_EXTERNAL_FILE_ACCESS=true.
            * If relative, resolved against the project root.
            * Always rejects paths that don't exist.
        """
        try:
            from core_logic.config import Config
            allow_external = bool(getattr(Config, "ALLOW_EXTERNAL_FILE_ACCESS", False))

            cleaned = (file_path or "").strip().strip('"').strip("'")
            if not cleaned:
                return None

            candidate = Path(cleaned).expanduser()
            root = DocumentSkill._get_project_root()

            if candidate.is_absolute():
                resolved = candidate.resolve()
            else:
                resolved = (root / candidate).resolve()

            # Guard: reject outside-project paths unless explicitly allowed.
            if not str(resolved).startswith(str(root)) and not allow_external:
                logger.warning(
                    f"Refusing external path '{resolved}'. "
                    "Set ALLOW_EXTERNAL_FILE_ACCESS=true in .env to permit."
                )
                return None

            if not resolved.exists():
                logger.warning(f"File not found: {resolved}")
                return None

            return resolved

        except Exception as e:
            logger.error(f"Path resolution error: {str(e)}")
            return None
    
    @staticmethod
    def read_text_file(file_path: str) -> Optional[str]:
        """
        Read a text file.
        
        Args:
            file_path: Path to text file
            
        Returns:
            File content or None if failed
        """
        try:
            safe_path = DocumentSkill._safe_path(file_path)
            if not safe_path or safe_path.suffix not in ['.txt', '.md']:
                return None
            
            with open(safe_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            logger.info(f"Read text file: {safe_path.name}")
            return content
        
        except Exception as e:
            logger.error(f"Failed to read text file: {str(e)}")
            return None
    
    @staticmethod
    def read_pdf(file_path: str, pages: Optional[List[int]] = None) -> Optional[str]:
        """
        Read a PDF file.
        
        Args:
            file_path: Path to PDF file
            pages: Specific pages to read (default: all)
            
        Returns:
            PDF text content or None if failed
        """
        try:
            import PyPDF2
        except ImportError:
            logger.warning("PyPDF2 not installed. Install with: pip install PyPDF2")
            return None
        
        try:
            safe_path = DocumentSkill._safe_path(file_path)
            if not safe_path or safe_path.suffix != '.pdf':
                return None
            
            text = ""
            with open(safe_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                num_pages = len(reader.pages)
                
                # Determine which pages to read
                pages_to_read = pages or list(range(num_pages))
                
                for page_num in pages_to_read:
                    if page_num < num_pages:
                        page = reader.pages[page_num]
                        text += f"\n--- Page {page_num + 1} ---\n"
                        text += page.extract_text()
            
            logger.info(f"Read PDF: {safe_path.name} ({len(pages_to_read)} pages)")
            return text
        
        except Exception as e:
            logger.error(f"Failed to read PDF: {str(e)}")
            return None
    
    @staticmethod
    def extract_metadata(file_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract file metadata.
        
        Args:
            file_path: Path to file
            
        Returns:
            Metadata dictionary or None
        """
        try:
            safe_path = DocumentSkill._safe_path(file_path)
            if not safe_path:
                return None
            
            metadata = {
                "filename": safe_path.name,
                "path": str(safe_path),
                "size_bytes": safe_path.stat().st_size,
                "modified": safe_path.stat().st_mtime,
                "format": safe_path.suffix.lstrip('.')
            }
            
            # PDF-specific metadata
            if safe_path.suffix == '.pdf':
                try:
                    import PyPDF2
                    with open(safe_path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        metadata["pages"] = len(reader.pages)
                        if reader.metadata:
                            metadata["pdf_metadata"] = {
                                "title": reader.metadata.get("/Title"),
                                "author": reader.metadata.get("/Author"),
                                "subject": reader.metadata.get("/Subject")
                            }
                except Exception as e:
                    logger.debug(f"Could not extract PDF metadata: {str(e)}")
            
            return metadata
        
        except Exception as e:
            logger.error(f"Failed to extract metadata: {str(e)}")
            return None
    
    @staticmethod
    def search_in_document(file_path: str, search_term: str, case_sensitive: bool = False) -> Optional[List[str]]:
        """
        Search for text in a document.
        
        Args:
            file_path: Path to file
            search_term: Text to search for
            case_sensitive: Whether search is case-sensitive
            
        Returns:
            List of matching lines or None if failed
        """
        try:
            content = None
            
            # Read file based on format
            safe_path = DocumentSkill._safe_path(file_path)
            if not safe_path:
                return None
            
            if safe_path.suffix == '.pdf':
                content = DocumentSkill.read_pdf(file_path)
            elif safe_path.suffix in ['.txt', '.md']:
                content = DocumentSkill.read_text_file(file_path)
            
            if not content:
                return None
            
            # Search
            lines = content.split('\n')
            matches = []
            
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(re.escape(search_term), flags)
            
            for i, line in enumerate(lines):
                if pattern.search(line):
                    matches.append(f"Line {i + 1}: {line.strip()}")
            
            logger.info(f"Found {len(matches)} matches for '{search_term}'")
            return matches if matches else []
        
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            return None
    
    @staticmethod
    def get_file_summary(file_path: str, max_lines: int = 10) -> Optional[str]:
        """
        Get a summary of file contents.
        
        Args:
            file_path: Path to file
            max_lines: Maximum lines to show
            
        Returns:
            File summary or None
        """
        try:
            content = None
            safe_path = DocumentSkill._safe_path(file_path)
            
            if not safe_path:
                return None
            
            # Read based on format
            if safe_path.suffix == '.pdf':
                content = DocumentSkill.read_pdf(file_path, pages=[0])
            elif safe_path.suffix in ['.txt', '.md']:
                content = DocumentSkill.read_text_file(file_path)
            
            if not content:
                return None
            
            # Create summary
            lines = content.split('\n')[:max_lines]
            summary = f"File: {safe_path.name}\n"
            summary += f"Format: {safe_path.suffix}\n"
            summary += f"Size: {safe_path.stat().st_size} bytes\n"
            summary += "\n--- Preview ---\n"
            summary += '\n'.join(lines)
            
            return summary
        
        except Exception as e:
            logger.error(f"Summary failed: {str(e)}")
            return None
    
    # ---- Gemini-powered summarisation --------------------------------------

    # Cap text sent to Gemini to keep latency / cost reasonable.
    # gemini-1.5-pro can take 1M tokens, but for a voice assistant we keep it tight.
    MAX_SUMMARY_CHARS = 60_000  # roughly 15k tokens

    @staticmethod
    def _read_any(file_path: str) -> Optional[str]:
        """Read text from a supported document (.pdf / .txt / .md) using the safe path."""
        safe_path = DocumentSkill._safe_path(file_path)
        if not safe_path:
            return None
        if safe_path.suffix.lower() == ".pdf":
            return DocumentSkill.read_pdf(file_path)
        if safe_path.suffix.lower() in (".txt", ".md"):
            return DocumentSkill.read_text_file(file_path)
        logger.warning(f"Unsupported format for summarisation: {safe_path.suffix}")
        return None

    @staticmethod
    def summarize_document(file_path: str, focus: Optional[str] = None) -> str:
        """
        Read a document and summarise it via the local LLM (or Gemini if configured).

        Args:
            file_path: Path to the document (.pdf, .txt, .md)
            focus: Optional aspect to focus the summary on
                   (e.g. "key findings", "action items").
        """
        content = DocumentSkill._read_any(file_path)
        if content is None:
            return (
                f"I couldn't open '{file_path}'. "
                "Check the path, or set ALLOW_EXTERNAL_FILE_ACCESS=true in .env "
                "if it's outside the project folder."
            )

        content = content.strip()
        if not content:
            return f"'{file_path}' appears to be empty or unreadable."

        truncated = len(content) > DocumentSkill.MAX_SUMMARY_CHARS
        snippet = content[: DocumentSkill.MAX_SUMMARY_CHARS]

        focus_clause = (
            f" Focus particularly on: {focus}." if focus else ""
        )
        prompt = (
            "Summarise the document below for a busy professional. Produce:\n"
            "  1. A one-sentence TL;DR.\n"
            "  2. Three to six concise bullet points covering the main ideas.\n"
            "  3. Any numbers, dates, or names that look important.\n"
            f"{focus_clause}\n"
            f"{'(Note: only the first part of a long document was provided.)' if truncated else ''}\n\n"
            "--- DOCUMENT START ---\n"
            f"{snippet}\n"
            "--- DOCUMENT END ---"
        )

        # Default to local LLM (Ollama). Use Gemini only if explicitly chosen.
        engine = os.getenv("SEARCH_ENGINE", "duckduckgo").lower()
        try:
            if engine == "gemini":
                from skills.gemini_search_skill import GeminiSearchSkill
                return GeminiSearchSkill.handle(prompt)
            from core_logic.llm_engine import LLMEngine
            return LLMEngine().generate(prompt, max_tokens=600, temperature=0.3)
        except Exception as e:
            logger.error(f"Summarisation failed: {e}")
            return f"Summarisation failed: {e}"

    # ---- main entry point ---------------------------------------------------

    # Patterns that indicate a "summarise" intent (checked before read/search)
    _SUMMARIZE_RE = re.compile(
        r"\b(?:summari[sz]e|summary\s+of|tl;?dr|give\s+me\s+a\s+summary)\b",
        re.IGNORECASE,
    )
    # Optional "focus on X" clause
    _FOCUS_RE = re.compile(
        r"\b(?:focus(?:ing)?\s+on|with\s+focus\s+on|highlight(?:ing)?)\s+(.+?)$",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_path(query: str) -> str:
        """
        Pull the most plausible file path from a free-form query.
        Accepts quoted strings or trailing tokens that look like paths.
        """
        # Quoted path wins
        m = re.search(r'["\']([^"\']+)["\']', query)
        if m:
            return m.group(1).strip()
        # Tokens with a slash, backslash, or known extension
        for tok in re.findall(r"\S+", query):
            if (
                "/" in tok
                or "\\" in tok
                or re.search(r"\.(pdf|txt|md)$", tok, re.IGNORECASE)
            ):
                return tok.strip(".,!?")
        return ""

    @staticmethod
    def handle(query: str) -> str:
        """Route a document-related query to read / search / summarise."""
        if not query or not query.strip():
            return "Tell me what you'd like me to do with which document, sir."

        # 1. Summarise intent
        if DocumentSkill._SUMMARIZE_RE.search(query):
            path = DocumentSkill._extract_path(query)
            if not path:
                return (
                    "Which document should I summarise, sir? "
                    "Try: 'summarize C:\\path\\to\\file.pdf'."
                )
            focus_match = DocumentSkill._FOCUS_RE.search(query)
            focus = focus_match.group(1).strip() if focus_match else None
            return DocumentSkill.summarize_document(path, focus=focus)

        query_lower = query.lower()

        # 2. Search-in-document intent
        search_match = re.search(
            r"search\s+(?:for\s+)?(.+?)\s+(?:in|inside)\s+(.+)$",
            query_lower,
        )
        if search_match:
            search_term = search_match.group(1).strip().strip('"\'')
            file_path = search_match.group(2).strip().strip('"\'')
            results = DocumentSkill.search_in_document(file_path, search_term)
            if results is None:
                return f"I couldn't search inside '{file_path}'."
            if not results:
                return f"No matches for '{search_term}' in {file_path}."
            return (
                f"Found {len(results)} matches:\n"
                + "\n".join(results[:5])
            )

        # 3. Read / open / show intent
        match = re.search(
            r"(?:read|open|show)\s+(?:file|document|pdf)?\s*(.+)$",
            query_lower,
        )
        if not match:
            return (
                "I can read, search, or summarise documents. Try:\n"
                "  - summarize C:\\path\\to\\file.pdf\n"
                "  - search for 'budget' in report.pdf\n"
                "  - read notes.md"
            )

        file_path = DocumentSkill._extract_path(query) or match.group(1).strip()
        summary = DocumentSkill.get_file_summary(file_path)
        if summary:
            return summary

        return f"Could not read document: {file_path}"
