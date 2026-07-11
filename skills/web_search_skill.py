"""
Open-source web search skill (DuckDuckGo + Ollama).

Pipeline:
    1. Strip the trigger phrase ("search for", "who is", "what is", ...).
    2. Fetch the top N results from DuckDuckGo via the `ddgs` package
       (no API key required, no rate limit beyond DuckDuckGo's own).
    3. Optionally pull each result's page text for richer context (best-effort).
    4. Build a synthesis prompt: "answer this question using ONLY these sources"
       and send it to the local Ollama LLM via the existing LLMEngine.
    5. Return the answer with a "Sources" footer for citations.

Fully open source / free / local LLM. The only network calls are to DuckDuckGo
and the source web pages. No API keys, no quotas.

Configuration (.env):
    WEB_SEARCH_RESULTS  - max results to fetch (default 5)
    WEB_SEARCH_REGION   - DuckDuckGo region code (default 'wt-wt' = world)
    WEB_SEARCH_TIMELIMIT - one of d/w/m/y or empty (default '' = any time)
    WEB_SEARCH_FETCH_PAGES - 'true' to fetch full pages for richer context
                             (default 'false', snippet-only is faster)
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)


_TRIGGER_RE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:hey\s+gemini|ask\s+gemini|use\s+gemini\s+for|"
    r"search(?:\s+for)?|google(?:\s+for)?|look\s+up|"
    r"tell\s+me\s+about|explain|define|"
    r"who(?:\s+is|\s+was|\s+are)?|"
    r"what(?:\s+is|\s+are|'?s)?|"
    r"why(?:\s+is|\s+does|\s+do)?|"
    r"when(?:\s+did|\s+is|\s+was)?|"
    r"where(?:\s+is|\s+are)?|"
    r"how(?:\s+does|\s+do|\s+is|\s+to|\s+can)?)\s+",
    re.IGNORECASE,
)


def _strip_trigger(query: str) -> str:
    cleaned = _TRIGGER_RE.sub("", query, count=1).strip()
    return cleaned or query.strip()


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


class WebSearchSkill:
    """DuckDuckGo + Ollama LLM. Lazy-initialised."""

    _ddgs = None
    _llm = None

    @classmethod
    def _get_ddgs(cls):
        if cls._ddgs is None:
            try:
                from ddgs import DDGS
            except ImportError as e:
                raise RuntimeError(
                    "ddgs not installed. Run: pip install ddgs"
                ) from e
            cls._ddgs = DDGS
        return cls._ddgs

    @classmethod
    def _get_llm(cls):
        if cls._llm is None:
            from core_logic.llm_engine import LLMEngine
            cls._llm = LLMEngine()
        return cls._llm

    @classmethod
    def _search(cls, query: str, max_results: int, region: str, timelimit: str) -> List[dict]:
        DDGS = cls._get_ddgs()
        try:
            with DDGS() as client:
                results = list(
                    client.text(
                        query,
                        region=region or "wt-wt",
                        safesearch="moderate",
                        timelimit=timelimit or None,
                        max_results=max_results,
                    )
                )
            return results or []
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []

    @classmethod
    def _fetch_page_text(cls, url: str, max_chars: int = 4000) -> str:
        try:
            import requests
            r = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (DAEMON-search)"},
            )
            if r.status_code != 200 or not r.text:
                return ""
            text = r.text
            # Crude HTML strip: remove <script>/<style>, then tags.
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return _truncate(text, max_chars)
        except Exception as e:
            logger.debug(f"page fetch failed for {url}: {e}")
            return ""

    @classmethod
    def handle(cls, text: str) -> str:
        if not text or not text.strip():
            return "What would you like me to look up, sir?"

        query = _strip_trigger(text)
        max_results = int(os.getenv("WEB_SEARCH_RESULTS", "5"))
        region = os.getenv("WEB_SEARCH_REGION", "wt-wt")
        timelimit = os.getenv("WEB_SEARCH_TIMELIMIT", "")
        fetch_pages = os.getenv("WEB_SEARCH_FETCH_PAGES", "false").lower() == "true"

        # Auto-detect news / current-events queries → force today's results
        _news_keywords = ("news", "today", "latest", "current", "happening",
                          "headlines", "right now", "this week", "recently")
        _is_news = any(kw in text.lower() for kw in _news_keywords)
        if _is_news:
            timelimit = timelimit or "d"   # limit to last 24 hours
            max_results = max(max_results, 7)  # more sources for news
            fetch_pages = True  # pull full article text

        results = cls._search(query, max_results, region, timelimit)
        if not results:
            return (
                "I couldn't reach any search results just now. "
                "Check your internet connection and try again."
            )

        # Build a numbered context block for the LLM.
        context_blocks = []
        sources = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip()
            url = (r.get("href") or r.get("url") or "").strip()
            snippet = (r.get("body") or "").strip()

            body = snippet
            if fetch_pages and url:
                page_text = cls._fetch_page_text(url)
                if page_text:
                    body = page_text

            context_blocks.append(
                f"[{i}] {title}\nURL: {url}\n{_truncate(body, 1500)}"
            )
            sources.append(f"[{i}] {title} ({url})" if title else f"[{i}] {url}")

        if _is_news:
            prompt = (
                "You're a voice assistant reading the latest news to the user. "
                "Use ONLY the sources below. Do NOT invent stories.\n"
                "Give 3 to 5 short news summaries, each ONE sentence (10-20 words). "
                "Start each with the topic in a natural way, e.g. 'In tech news, ...'. "
                "Use plain spoken English — no bullet symbols, no markdown, no URLs. "
                "After the headlines say: 'Want me to go deeper on any of those?'\n\n"
                f"Question: {query}\n\n"
                "Sources:\n" + "\n\n".join(context_blocks)
            )
        else:
            prompt = (
                "You're answering a question OUT LOUD for a voice assistant user. "
                "Use ONLY the sources below — don't invent anything. "
                "Reply like a real person chatting, not a research paper:\n"
                "  - ONE or TWO short sentences for simple questions (15-40 words).\n"
                "  - Use contractions (it's, he's, they're). Skip lists, headings, markdown.\n"
                "  - Don't quote URLs or [1]/[2] markers aloud — they sound terrible.\n"
                "  - End with a brief offer like 'want the details?' or 'want me to go deeper?'"
                "    if the topic has more depth.\n"
                "  - If the sources don't have the answer, say so plainly.\n\n"
                f"Question: {query}\n\n"
                "Sources (for your reference only — do not read these aloud):\n"
                + "\n\n".join(context_blocks)
            )

        try:
            llm = cls._get_llm()
        except Exception as e:
            logger.error(f"LLM init failed: {e}")
            return f"Local LLM unavailable: {e}"

        try:
            answer = llm.generate(prompt, max_tokens=320 if _is_news else 180, temperature=0.5)
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return f"Local LLM error: {e}"

        answer = (answer or "").strip()
        if not answer:
            answer = "I couldn't get a clear answer from the sources."

        # Don't append a Sources block — that gets spoken aloud and ruins the flow.
        # Sources are still logged for debugging.
        logger.info("Search sources: " + " | ".join(sources))
        return answer
