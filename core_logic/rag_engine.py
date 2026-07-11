"""
RAG Engine — Retrieval-Augmented Generation
=============================================

Gives D.A.E.M.O.N. grounded answers by searching a local vector database
of the user's documents and code before passing context to the LLM.

Architecture::

    User Query
        │
        ▼
    ┌────────────────┐   top-K chunks    ┌──────────┐
    │  ChromaDB      │ ────────────────▶ │   LLM    │ → Grounded Answer
    │  (embeddings)  │                   └──────────┘
    └────────────────┘
        ▲
        │  ingest on startup / /index command
    ┌───────────────────┐
    │ documents/ + code  │
    └───────────────────┘

Dependencies:
    pip install chromadb sentence-transformers
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core_logic.config import Config

logger = logging.getLogger(__name__)

# File extensions we can index
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bat",
    ".c", ".h", ".cpp", ".java", ".rs", ".go", ".rb", ".php",
    ".csv", ".log", ".xml", ".sql", ".env.example",
}
_PDF_EXTENSIONS = {".pdf"}
_ALL_EXTENSIONS = _TEXT_EXTENSIONS | _PDF_EXTENSIONS


class RAGEngine:
    """Local RAG pipeline using ChromaDB + sentence-transformers.

    Parameters
    ----------
    index_dirs
        Directories to recursively scan for documents.
    db_path
        Path for the ChromaDB persistent storage.
    chunk_size
        Approximate number of characters per chunk (~4 chars ≈ 1 token).
    chunk_overlap
        Character overlap between adjacent chunks.
    top_k
        Number of chunks to retrieve per query.
    embedding_model
        Sentence-transformer model name (downloaded on first use).
    """

    def __init__(
        self,
        index_dirs: Optional[List[Path]] = None,
        db_path: Optional[Path] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        top_k: int = 5,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._chunk_size = chunk_size * 4  # convert token estimate → chars
        self._chunk_overlap = chunk_overlap * 4
        self._top_k = top_k
        self._model_name = embedding_model
        self._db_path = db_path or Config.RAG_DB_PATH
        self._lock = threading.Lock()

        # Directories to index
        self._index_dirs: List[Path] = index_dirs or []
        # Always include documents/ and daemon_workspace/ if they exist
        for d in [Config.NOVA_DOCS_DIR, Config.WORKSPACE_ROOT]:
            if d not in self._index_dirs:
                self._index_dirs.append(d)
        # Add user-configured extra dirs
        if Config.RAG_DIRS:
            for extra in Config.RAG_DIRS.split(","):
                p = Path(extra.strip())
                if p.exists() and p not in self._index_dirs:
                    self._index_dirs.append(p)

        # Lazy-loaded components (heavy imports deferred)
        self._embedder = None
        self._collection = None
        self._ready = False

        logger.info(
            f"🧠 RAGEngine created — dirs={[str(d) for d in self._index_dirs]}, "
            f"db={self._db_path}, model={self._model_name}"
        )

    # ------------------------------------------------------------------
    # Lazy initialization (avoids slow imports at startup)
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        """Initialize ChromaDB and sentence-transformers on first use."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                import chromadb
                from sentence_transformers import SentenceTransformer

                self._db_path.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(self._db_path))
                self._collection = client.get_or_create_collection(
                    name="daemon_rag",
                    metadata={"hnsw:space": "cosine"},
                )
                self._embedder = SentenceTransformer(self._model_name)
                self._ready = True
                logger.info(
                    f"🧠 RAGEngine ready — {self._collection.count()} chunks in store"
                )
            except ImportError as e:
                logger.warning(
                    f"🧠 RAG dependencies missing ({e}). "
                    f"Run: pip install chromadb sentence-transformers"
                )
                raise
            except Exception as e:
                logger.error(f"🧠 RAG initialization failed: {e}")
                raise

    # ------------------------------------------------------------------
    # Document ingestion
    # ------------------------------------------------------------------

    def _file_hash(self, path: Path) -> str:
        """Fast content hash for change detection."""
        h = hashlib.md5()
        try:
            h.update(path.read_bytes())
        except Exception:
            h.update(str(path).encode())
        return h.hexdigest()

    def _extract_text(self, path: Path) -> str:
        """Extract plain text from a file."""
        suffix = path.suffix.lower()

        if suffix in _PDF_EXTENSIONS:
            try:
                import PyPDF2
                text_parts = []
                with open(path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        t = page.extract_text()
                        if t:
                            text_parts.append(t)
                return "\n".join(text_parts)
            except Exception as e:
                logger.warning(f"PDF extraction failed for {path}: {e}")
                return ""

        if suffix in _TEXT_EXTENSIONS:
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Text read failed for {path}: {e}")
                return ""

        return ""

    def _chunk_text(self, text: str, source: str) -> List[Dict[str, str]]:
        """Split text into overlapping chunks with source metadata."""
        if not text.strip():
            return []

        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk_text = text[start:end]

            # Try to break at a paragraph or sentence boundary
            if end < len(text):
                for sep in ["\n\n", "\n", ". ", "! ", "? "]:
                    last_sep = chunk_text.rfind(sep)
                    if last_sep > self._chunk_size // 2:
                        chunk_text = chunk_text[: last_sep + len(sep)]
                        end = start + len(chunk_text)
                        break

            if chunk_text.strip():
                chunks.append({
                    "text": chunk_text.strip(),
                    "source": source,
                    "chunk_idx": idx,
                })
                idx += 1

            start = end - self._chunk_overlap
            if start <= 0 and idx > 0:
                break  # safety

        return chunks

    def ingest_file(self, path: Path) -> int:
        """Index a single file. Returns number of chunks created."""
        self._ensure_ready()

        file_hash = self._file_hash(path)
        source = str(path)

        # Check if already indexed with same hash
        existing = self._collection.get(
            where={"source": source},
            include=["metadatas"],
        )
        if existing and existing["ids"]:
            # Check if hash matches (file unchanged)
            for meta in (existing.get("metadatas") or []):
                if meta.get("hash") == file_hash:
                    return 0  # already up-to-date
            # File changed — delete old chunks first
            self._collection.delete(ids=existing["ids"])

        text = self._extract_text(path)
        if not text.strip():
            return 0

        chunks = self._chunk_text(text, source)
        if not chunks:
            return 0

        # Embed and store
        texts = [c["text"] for c in chunks]
        embeddings = self._embedder.encode(texts).tolist()
        ids = [f"{file_hash}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source": source,
                "chunk_idx": c["chunk_idx"],
                "hash": file_hash,
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }
            for c in chunks
        ]

        self._collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        return len(chunks)

    def ingest_all(self) -> Dict[str, int]:
        """Scan all configured directories and index new/changed files.

        Returns a summary dict: {dir_path: num_chunks_added}.
        """
        self._ensure_ready()
        summary: Dict[str, int] = {}

        for dir_path in self._index_dirs:
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
                continue

            total = 0
            for root, _dirs, files in os.walk(dir_path):
                # Skip hidden dirs, __pycache__, node_modules, .git, venv
                root_path = Path(root)
                skip = False
                for part in root_path.parts:
                    if part.startswith(".") or part in (
                        "__pycache__", "node_modules", "venv", ".git",
                    ):
                        skip = True
                        break
                if skip:
                    continue

                for fname in files:
                    fpath = root_path / fname
                    if fpath.suffix.lower() in _ALL_EXTENSIONS:
                        try:
                            n = self.ingest_file(fpath)
                            total += n
                        except Exception as e:
                            logger.warning(f"RAG ingest failed for {fpath}: {e}")

            summary[str(dir_path)] = total

        total_chunks = sum(summary.values())
        total_stored = self._collection.count() if self._collection else 0
        logger.info(
            f"🧠 RAG indexing complete — {total_chunks} new chunk(s), "
            f"{total_stored} total in store"
        )
        return summary

    def ingest_background(self) -> None:
        """Run ingest_all on a background thread."""
        t = threading.Thread(
            target=self._safe_ingest, name="RAGIndexer", daemon=True
        )
        t.start()

    def _safe_ingest(self) -> None:
        try:
            self.ingest_all()
        except Exception as e:
            logger.error(f"🧠 Background RAG indexing failed: {e}")

    # ------------------------------------------------------------------
    # Query / retrieval
    # ------------------------------------------------------------------

    def query(self, text: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve the most relevant chunks for a query.

        Returns a list of dicts with keys: text, source, score, chunk_idx.
        """
        self._ensure_ready()
        k = top_k or self._top_k

        if self._collection.count() == 0:
            return []

        embedding = self._embedder.encode([text]).tolist()

        results = self._collection.query(
            query_embeddings=embedding,
            n_results=min(k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        chunks = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                dist = results["distances"][0][i] if results.get("distances") else 0
                chunks.append({
                    "text": doc,
                    "source": meta.get("source", "unknown"),
                    "chunk_idx": meta.get("chunk_idx", 0),
                    "score": 1.0 - dist,  # cosine distance → similarity
                })

        return chunks

    def augment_prompt(
        self,
        user_text: str,
        prior_context: Optional[List[Dict[str, str]]] = None,
        min_score: float = 0.40,
    ) -> Optional[List[Dict[str, str]]]:
        """Inject RAG context into the conversation history.

        Returns the augmented context list, or the original if no relevant
        chunks were found.
        """
        try:
            chunks = self.query(user_text)
        except Exception as e:
            logger.warning(f"RAG query failed: {e}")
            return prior_context

        # Filter by minimum relevance
        relevant = [c for c in chunks if c["score"] >= min_score]
        if not relevant:
            return prior_context

        # Build context injection
        rag_block = "Here is relevant information from the user's documents and code:\n\n"
        for c in relevant:
            source_name = Path(c["source"]).name
            rag_block += f"[From {source_name}]:\n{c['text']}\n\n"
        rag_block += (
            "Use this information to ground your answer. "
            "If the information doesn't help, ignore it and answer naturally."
        )

        # Prepend as a system-level context message
        rag_message = {"role": "system", "content": rag_block}
        if prior_context:
            return [rag_message] + prior_context
        return [rag_message]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return stats about the RAG store."""
        if not self._ready:
            return {"status": "not_initialized", "chunks": 0}
        return {
            "status": "ready",
            "chunks": self._collection.count() if self._collection else 0,
            "dirs": [str(d) for d in self._index_dirs],
            "model": self._model_name,
            "db_path": str(self._db_path),
        }
"""
RAG Engine for D.A.E.M.O.N. — provides grounded answers from local documents.
"""
