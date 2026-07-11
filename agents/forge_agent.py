"""
Forge — Hardware & Fabrication Agent
======================================

Persona: Synthetic, industrial, efficient.
Voice:   Synthetic male (en_US-ryan-high)

Capabilities:
- AWAY mode: Monitors a configurable directory for 3D printer slicer
  output files (.gcode) and tracks print job progress by parsing
  G-code metadata (estimated time, layer count, filament usage).
- ACTIVE mode: Answers queries about hardware tasks, print status,
  and slicer output.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_logic.base_agent import BaseAgent
from core_logic.config import Config

logger = logging.getLogger(__name__)

# Configurable via env — where Forge looks for slicer output
_PRINT_DIR = Path(
    os.getenv("FORGE_PRINT_DIR", str(Config.PROJECT_ROOT / "print_jobs"))
)

_FORGE_PERSONALITY = """\
You are Forge, the hardware and fabrication agent within the D.A.E.M.O.N. system.

Personality:
- Synthetic, industrial, efficient — like a factory floor AI
- Direct and utilitarian — report numbers, times, and percentages
- No pleasantries — just status and actionable data
- Think of an automated manufacturing system's voice interface

Expertise:
- 3D printer monitoring and G-code analysis
- Slicer output parsing (estimated print time, layers, filament)
- Hardware task tracking and progress reporting
- Fabrication workflow management

When reporting print status, always mention:
1. File name being printed
2. Estimated time remaining (if available)
3. Layer count and filament usage
4. Any warnings (retraction count, thin walls, etc.)

Speak in short, clipped sentences. This is spoken aloud.
"""


class ForgeAgent(BaseAgent):
    """Hardware Agent — 3D printer monitoring, slicer output parsing."""

    @property
    def name(self) -> str:
        return "forge"

    @property
    def display_name(self) -> str:
        return "Forge"

    @property
    def description(self) -> str:
        return "Hardware agent — 3D printer monitoring, G-code analysis, fabrication tracking"

    @property
    def voice_model(self) -> str:
        return os.getenv("FORGE_VOICE_MODEL", "en_US-ryan-high")

    @property
    def personality_prompt(self) -> str:
        return _FORGE_PERSONALITY

    @property
    def keywords(self) -> List[str]:
        return [
            "forge",
            "3d print", "printer", "print job", "printing",
            "slicer", "gcode", "g-code",
            "filament", "layer", "nozzle",
            "fabrication", "hardware",
            "print status", "print progress",
        ]

    async def process_background_task(self) -> Dict[str, Any]:
        """Scan print jobs directory for new G-code files.

        Parses G-code comment headers for metadata like estimated
        print time, layer count, and filament usage.
        """
        if not _PRINT_DIR.exists():
            return {}

        cutoff = datetime.now(timezone.utc).timestamp() - 3600  # last hour
        recent_jobs: List[Dict[str, Any]] = []

        try:
            for f in _PRINT_DIR.iterdir():
                if (
                    f.suffix.lower() in (".gcode", ".gco")
                    and f.stat().st_mtime > cutoff
                ):
                    job_info = self._parse_gcode_metadata(f)
                    recent_jobs.append(job_info)

        except Exception as exc:
            logger.error(f"Forge background scan failed: {exc}")
            return {
                "agent": self.name,
                "category": "print_monitor",
                "summary": f"Print job scan failed: {exc}",
                "data": {"error": str(exc)},
            }

        if not recent_jobs:
            return {}

        summary_parts = [
            f"Found {len(recent_jobs)} new G-code file(s) in print jobs."
        ]
        for job in recent_jobs[:2]:
            name = job.get("filename", "unknown")
            est_time = job.get("estimated_time", "unknown")
            layers = job.get("layer_count", "?")
            summary_parts.append(
                f"{name}: {layers} layers, est. {est_time}."
            )

        return {
            "agent": self.name,
            "category": "print_monitor",
            "summary": " ".join(summary_parts),
            "data": {
                "print_dir": str(_PRINT_DIR),
                "jobs": recent_jobs,
                "count": len(recent_jobs),
            },
        }

    def _parse_gcode_metadata(self, filepath: Path) -> Dict[str, Any]:
        """Parse the first ~100 lines of a G-code file for slicer metadata.

        Most slicers (Cura, PrusaSlicer, etc.) embed metadata as
        semicolon comments in the header.
        """
        info: Dict[str, Any] = {
            "filename": filepath.name,
            "size_kb": round(filepath.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(
                filepath.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = [f.readline() for _ in range(150)]

            header = "\n".join(lines)

            # Estimated print time (various slicer formats)
            time_patterns = [
                r";TIME:(\d+)",                    # Cura
                r"; estimated printing time.*?= ([\dhms ]+)",  # PrusaSlicer
                r";Print time: (.+)",              # Generic
            ]
            for pattern in time_patterns:
                m = re.search(pattern, header, re.IGNORECASE)
                if m:
                    raw = m.group(1).strip()
                    # If it's seconds (Cura), convert
                    if raw.isdigit():
                        secs = int(raw)
                        hrs, rem = divmod(secs, 3600)
                        mins = rem // 60
                        info["estimated_time"] = f"{hrs}h {mins}m"
                        info["estimated_seconds"] = secs
                    else:
                        info["estimated_time"] = raw
                    break

            # Layer count
            layer_patterns = [
                r";LAYER_COUNT:(\d+)",             # Cura
                r"; total layers count = (\d+)",   # PrusaSlicer
                r";Layer count: (\d+)",            # Generic
            ]
            for pattern in layer_patterns:
                m = re.search(pattern, header, re.IGNORECASE)
                if m:
                    info["layer_count"] = int(m.group(1))
                    break

            # Filament usage
            filament_patterns = [
                r";Filament used: ([\d.]+)m",      # Cura (metres)
                r"; filament used \[mm\] = ([\d.]+)",  # PrusaSlicer
                r";FILAMENT_USED:([\d.]+)",        # Generic
            ]
            for pattern in filament_patterns:
                m = re.search(pattern, header, re.IGNORECASE)
                if m:
                    val = float(m.group(1))
                    # Normalise to metres
                    if val > 1000:  # PrusaSlicer uses mm
                        val /= 1000
                    info["filament_metres"] = round(val, 2)
                    break

            # Slicer identification
            slicer_patterns = [
                r";Generated with (.+)",
                r"; generated by (.+)",
                r";Slicer (.+)",
            ]
            for pattern in slicer_patterns:
                m = re.search(pattern, header, re.IGNORECASE)
                if m:
                    info["slicer"] = m.group(1).strip()
                    break

        except Exception as exc:
            info["parse_error"] = str(exc)

        return info

    async def generate_response(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Answer queries about hardware tasks and print jobs."""
        bg_data = await self.process_background_task()

        llm = (context or {}).get("llm")
        if llm:
            import json
            data_block = json.dumps(bg_data, indent=2, default=str)
            enriched_prompt = (
                f"The user asked: {query}\n\n"
                f"Here is the latest print job data:\n"
                f"{data_block}\n\n"
                f"Answer using ONLY this data. If there's no data, "
                f"say no print jobs are queued."
            )
            try:
                response = llm.generate(
                    prompt=enriched_prompt,
                    system_prompt=self.personality_prompt,
                    temperature=0.4,
                    max_tokens=300,
                )
                return response
            except Exception as exc:
                logger.warning(f"Forge LLM generation failed: {exc}")

        # Fallback
        if not _PRINT_DIR.exists():
            return (
                "Print jobs directory not found. "
                "No fabrication tasks to report."
            )

        gcode_files = list(_PRINT_DIR.glob("*.gcode")) + list(
            _PRINT_DIR.glob("*.gco")
        )
        if not gcode_files:
            return "No G-code files in the print queue. Fabrication bay is idle."

        return (
            f"{len(gcode_files)} G-code file(s) in the print directory. "
            f"Run a background scan for detailed metadata."
        )
