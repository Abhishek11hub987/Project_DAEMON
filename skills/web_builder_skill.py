"""
Web Builder Skill — Voice-Activated Website Generation
========================================================

Routes commands like "build me a portfolio website" through the existing
Orchestrator (Architect → Developer → QA) with web-specific constraints.

The generated HTML/CSS/JS files are saved to::

    daemon_workspace/web_projects/<project_name>/

and the index.html is automatically opened in the default browser.
"""

from __future__ import annotations

import logging
import os
import re
import webbrowser
from pathlib import Path
from typing import Optional

from core_logic.config import Config

logger = logging.getLogger(__name__)


# Map user intents to structured task descriptions
_WEB_TEMPLATES = {
    "portfolio": (
        "Build a modern, visually stunning single-page portfolio website. "
        "Include sections: Hero with name and tagline, About Me, Skills "
        "(with progress bars or tags), Projects (card grid with hover effects), "
        "and Contact form. Use a dark theme with accent colours, smooth scroll, "
        "and CSS animations. Create index.html, style.css, and script.js."
    ),
    "landing": (
        "Build a professional landing page for a product or service. "
        "Include: Hero section with CTA button, Features grid, Testimonials, "
        "Pricing cards, and Footer. Use a clean modern design with gradients "
        "and subtle animations. Create index.html, style.css, and script.js."
    ),
    "blog": (
        "Build a clean, readable blog website. Include: Header with nav, "
        "Featured post hero, Blog post cards in a grid, Sidebar with categories "
        "and recent posts, and Footer. Use elegant typography (Google Fonts), "
        "dark/light toggle. Create index.html, style.css, and script.js."
    ),
    "dashboard": (
        "Build a data dashboard UI with sidebar navigation, stat cards at the top, "
        "chart placeholders (use CSS-only bar charts), a recent activity table, "
        "and a dark theme. Responsive layout with CSS Grid. "
        "Create index.html, style.css, and script.js."
    ),
    "ecommerce": (
        "Build an e-commerce product listing page. Include: Header with search "
        "and cart icon, Product grid with cards (image, name, price, add-to-cart), "
        "Filters sidebar, and Footer. Modern design with hover effects. "
        "Create index.html, style.css, and script.js."
    ),
}

# Keywords that hint at a specific template
_TEMPLATE_HINTS = {
    "portfolio": ["portfolio", "personal", "resume", "cv", "about me"],
    "landing": ["landing", "product", "startup", "service", "saas"],
    "blog": ["blog", "article", "post", "journal", "writing"],
    "dashboard": ["dashboard", "admin", "analytics", "stats", "panel"],
    "ecommerce": ["ecommerce", "e-commerce", "shop", "store", "product listing"],
}


def _detect_template(text: str) -> str:
    """Detect which template the user wants from their natural language."""
    text_lower = text.lower()
    for template, hints in _TEMPLATE_HINTS.items():
        for hint in hints:
            if hint in text_lower:
                return template
    return "portfolio"  # sensible default


def _sanitize_project_name(text: str) -> str:
    """Extract a project name from the user's request."""
    # Try to find "called X" or "named X"
    match = re.search(r'(?:called|named)\s+["\']?(\w[\w\s-]*)', text, re.I)
    if match:
        return re.sub(r'[^\w-]', '_', match.group(1).strip().lower())

    # Use template type
    template = _detect_template(text)
    return f"my_{template}_site"


class WebBuilderSkill:
    """Skill handler for website generation via the Orchestrator."""

    @staticmethod
    def handle(text: str) -> str:
        """Handle a web build request.

        This is called by the SkillRouter. It prepares the task and
        delegates to the Orchestrator. Returns a TTS-friendly summary.
        """
        template = _detect_template(text)
        project_name = _sanitize_project_name(text)
        task_desc = _WEB_TEMPLATES.get(template, _WEB_TEMPLATES["portfolio"])

        # Add user's custom requirements if they said more than just "build a website"
        extra = text.lower()
        for word in ["build", "make", "create", "design", "me", "a", "an", "the",
                      "website", "webpage", "web page", "site", "please"]:
            extra = extra.replace(word, "")
        extra = extra.strip()
        if len(extra) > 10:
            task_desc += f"\n\nAdditional user requirements: {extra}"

        # Ensure project directory exists
        project_dir = Config.WORKSPACE_ROOT / "web_projects" / project_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Build the full task for the orchestrator
        full_task = (
            f"Create a website project in the directory 'web_projects/{project_name}/'. "
            f"{task_desc}\n\n"
            f"IMPORTANT: All files must be self-contained (no npm, no build tools). "
            f"Use vanilla HTML5, CSS3, and JavaScript only. "
            f"Use Google Fonts via CDN link. "
            f"The website must look stunning and modern — not a basic template. "
            f"Include responsive design for mobile."
        )

        logger.info(f"🌐 WebBuilder: template={template}, project={project_name}")

        # Return the task description — the main process_command will
        # detect SkillType.WEB_BUILD and route to the orchestrator.
        # We store the task in a module-level variable for the orchestrator to pick up.
        WebBuilderSkill._last_task = full_task
        WebBuilderSkill._last_project_dir = project_dir
        WebBuilderSkill._last_project_name = project_name

        return full_task

    @staticmethod
    def open_result(project_dir: Optional[Path] = None) -> str:
        """Open the generated website in the default browser."""
        d = project_dir or getattr(WebBuilderSkill, "_last_project_dir", None)
        if not d:
            return "No project directory found."

        index = d / "index.html"
        if index.exists():
            webbrowser.open(str(index))
            return f"Opened {index} in your browser."
        return f"No index.html found in {d}."

    # Class-level storage for inter-call state
    _last_task: Optional[str] = None
    _last_project_dir: Optional[Path] = None
    _last_project_name: Optional[str] = None
