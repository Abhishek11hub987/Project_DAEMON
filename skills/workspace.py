"""
Secure Workspace Sandbox — Pillar B
====================================

Gives the LLM "hands" by providing sandboxed file I/O and command execution.

**Security model (defense-in-depth)**:

1. **Path canonicalization** — every user-supplied path is resolved to an
   absolute real path and checked against the workspace root via
   ``os.path.commonpath``.  Symlink escapes are blocked because we resolve
   *after* creation and re-validate.
2. **Traversal rejection** — paths containing ``..`` are rejected outright
   before any resolution, as an early-exit heuristic.
3. **Command denylist** — known-destructive commands (``rm -rf /``,
   ``format``, ``shutdown``, …) are blocked before the shell is invoked.
4. **Subprocess sandboxing** — commands run with a hard timeout, CWD pinned
   to the workspace root, and ``stdout``/``stderr`` captured.
5. **Size limits** — reads capped at ``SANDBOX_MAX_READ_BYTES``, writes at
   ``SANDBOX_MAX_WRITE_BYTES``.
6. **Audit logging** — every tool call is logged with arguments and result.

All limits are configurable via ``.env`` / ``Config``.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_logic.config import Config
from core_logic.error_handler import SandboxSecurityError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Denylist: patterns matched case-insensitively against the raw command string
# ---------------------------------------------------------------------------
_DENIED_COMMAND_PATTERNS: List[str] = [
    # Linux / macOS destructive
    r"\brm\s+.*-\s*r.*\s+/\s*$",       # rm -rf /
    r"\brm\s+.*-\s*r.*\s+/[a-z]",      # rm -rf /etc, /usr, …
    r"\bmkfs\b",                         # format filesystem
    r"\bdd\s+if=",                       # raw disk write
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",  # fork bomb
    r"\bchmod\s+777\s+/",               # open root perms
    r"\bchown\s+.*\s+/",               # take ownership of root
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+[0-6]",
    r"\bsystemctl\s+(halt|poweroff)",
    r"\bkillall\b",
    r"\bpkill\s+-9",
    # Windows destructive
    r"\bformat\s+[a-zA-Z]:",            # format C:
    r"\bdel\s+/[sS]",                   # del /s (recursive delete)
    r"\brd\s+/[sS]",                    # rd /s
    r"\brmdir\s+/[sS]",
    r"\bregedit\b",
    r"\breg\s+(add|delete)",
    r"\bnet\s+user\b",
    r"\bnet\s+stop\b",
    r"\bbcdedit\b",
    r"\bdiskpart\b",
    r"\bschtasks\b",
    r"\btakeown\b",
    r"\bicacls\b",
    # Cross-platform dangerous
    r"\bcurl\s+.*\|\s*(ba)?sh",         # curl | bash
    r"\bwget\s+.*\|\s*(ba)?sh",
    r"\bpowershell\s+.*-enc",           # encoded powershell payloads
    r"\bnc\s+-[el]",                    # netcat listeners
    r"\bpython[23]?\s+-c\s+.*import\s+os.*system",
]

_DENIED_RE = [re.compile(p, re.IGNORECASE) for p in _DENIED_COMMAND_PATTERNS]


class WorkspaceSandbox:
    """Secure sandboxed workspace for LLM-driven file and command operations.

    Every public method returns a plain ``str`` result suitable for feeding
    back into the LLM as a tool-call response.

    Parameters
    ----------
    workspace_root
        Absolute path to the sandbox root.  Defaults to
        ``Config.WORKSPACE_ROOT`` (typically ``~/daemon_workspace``).
    """

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.root: Path = (workspace_root or Config.WORKSPACE_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

        self._max_read: int = Config.SANDBOX_MAX_READ_BYTES
        self._max_write: int = Config.SANDBOX_MAX_WRITE_BYTES
        self._cmd_timeout: int = Config.SANDBOX_COMMAND_TIMEOUT
        self._is_windows: bool = platform.system() == "Windows"

        logger.info(
            f"🔒 WorkspaceSandbox initialised — root: {self.root}  "
            f"(read limit: {self._max_read // 1024}KB, "
            f"write limit: {self._max_write // (1024*1024)}MB, "
            f"cmd timeout: {self._cmd_timeout}s)"
        )

    # ------------------------------------------------------------------
    # Path validation (the critical security layer)
    # ------------------------------------------------------------------

    def _validate_path(self, user_path: str) -> Path:
        """Resolve *user_path* relative to the workspace root and ensure it
        stays inside the sandbox.

        Raises
        ------
        SandboxSecurityError
            If the resolved path escapes the workspace root.
        """
        # Early heuristic: reject obvious traversal attempts before touching
        # the filesystem at all.
        if ".." in user_path:
            raise SandboxSecurityError(
                f"Path traversal blocked — '..' is not allowed: {user_path!r}"
            )

        # Resolve relative to workspace root.
        candidate = (self.root / user_path).resolve()

        # The canonical workspace root (already resolved in __init__).
        try:
            common = os.path.commonpath([str(self.root), str(candidate)])
        except ValueError:
            # On Windows, commonpath raises ValueError when paths are on
            # different drives (e.g. C: vs D:).
            raise SandboxSecurityError(
                f"Path is outside the workspace (different drive): {user_path!r}"
            )

        if Path(common).resolve() != self.root:
            raise SandboxSecurityError(
                f"Path escapes the workspace sandbox: {user_path!r} "
                f"(resolved to {candidate})"
            )

        return candidate

    def _validate_real_path(self, resolved: Path) -> Path:
        """Post-creation check: resolve *symlinks* and re-validate.

        Call this **after** a file/directory has been created to catch symlink
        attacks that redirect into host-system paths.
        """
        real = resolved.resolve(strict=True)
        try:
            common = os.path.commonpath([str(self.root), str(real)])
        except ValueError:
            raise SandboxSecurityError(
                f"Symlink escape detected (different drive): {resolved} → {real}"
            )
        if Path(common).resolve() != self.root:
            # Undo the write — the file shouldn't exist outside the sandbox.
            try:
                real.unlink(missing_ok=True)
            except Exception:
                pass
            raise SandboxSecurityError(
                f"Symlink escape detected: {resolved} → {real}"
            )
        return real

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def write_file(self, path: str, content: str) -> str:
        """Create or overwrite a file inside the workspace.

        Parameters
        ----------
        path
            Relative path within the workspace (e.g. ``src/main.py``).
        content
            Full file content as a string.

        Returns
        -------
        str
            Human-readable success/failure message.
        """
        t0 = time.monotonic()
        try:
            encoded = content.encode("utf-8")
            if len(encoded) > self._max_write:
                msg = (
                    f"Write rejected — content is {len(encoded):,} bytes, "
                    f"limit is {self._max_write:,} bytes."
                )
                logger.warning(f"[sandbox:write_file] {msg}")
                return msg

            target = self._validate_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)

            # Post-creation symlink check.
            self._validate_real_path(target)

            elapsed = time.monotonic() - t0
            msg = (
                f"✅ Wrote {len(encoded):,} bytes to {path} "
                f"({elapsed:.2f}s)"
            )
            logger.info(f"[sandbox:write_file] {msg}")
            return msg

        except SandboxSecurityError:
            raise
        except Exception as e:
            msg = f"❌ write_file failed for {path!r}: {e}"
            logger.error(f"[sandbox:write_file] {msg}", exc_info=True)
            return msg

    def read_file(self, path: str) -> str:
        """Read the contents of a file inside the workspace.

        Parameters
        ----------
        path
            Relative path within the workspace.

        Returns
        -------
        str
            The file contents (or an error message).
        """
        try:
            target = self._validate_path(path)
            if not target.exists():
                return f"❌ File not found: {path}"
            if not target.is_file():
                return f"❌ Not a file: {path}"

            size = target.stat().st_size
            if size > self._max_read:
                return (
                    f"❌ File too large to read: {size:,} bytes "
                    f"(limit {self._max_read:,} bytes). "
                    f"Use a command like 'head -n 100 {path}' instead."
                )

            text = target.read_text(encoding="utf-8", errors="replace")
            logger.info(
                f"[sandbox:read_file] Read {len(text):,} chars from {path}"
            )
            return text

        except SandboxSecurityError:
            raise
        except Exception as e:
            msg = f"❌ read_file failed for {path!r}: {e}"
            logger.error(f"[sandbox:read_file] {msg}", exc_info=True)
            return msg

    def list_files(self, path: str = ".") -> str:
        """List files and directories inside the workspace.

        Parameters
        ----------
        path
            Relative directory path within the workspace (default: root).

        Returns
        -------
        str
            A formatted directory listing.
        """
        try:
            target = self._validate_path(path)
            if not target.exists():
                return f"❌ Directory not found: {path}"
            if not target.is_dir():
                return f"❌ Not a directory: {path}"

            entries: List[str] = []
            for child in sorted(target.iterdir()):
                rel = child.relative_to(self.root)
                kind = "📁" if child.is_dir() else "📄"
                size_info = ""
                if child.is_file():
                    size_info = f"  ({child.stat().st_size:,} bytes)"
                entries.append(f"  {kind} {rel}{size_info}")

            if not entries:
                return f"(empty directory: {path})"

            header = f"📂 {path}/ — {len(entries)} item(s):\n"
            logger.info(
                f"[sandbox:list_files] Listed {len(entries)} entries in {path}"
            )
            return header + "\n".join(entries)

        except SandboxSecurityError:
            raise
        except Exception as e:
            msg = f"❌ list_files failed for {path!r}: {e}"
            logger.error(f"[sandbox:list_files] {msg}", exc_info=True)
            return msg

    def execute_sandbox_command(self, cmd: str) -> str:
        """Run a shell command with the CWD pinned to the workspace root.

        The command is checked against a denylist of known-dangerous patterns
        before execution.

        Parameters
        ----------
        cmd
            The shell command string to execute.

        Returns
        -------
        str
            Combined stdout + stderr output, or an error message.
        """
        t0 = time.monotonic()

        # ---- security check: command denylist ----
        for pattern in _DENIED_RE:
            if pattern.search(cmd):
                msg = f"🛑 Command BLOCKED by security policy: {cmd!r}"
                logger.warning(f"[sandbox:execute] {msg}")
                raise SandboxSecurityError(msg)

        # ---- execute ----
        try:
            if self._is_windows:
                shell_cmd = ["cmd.exe", "/c", cmd]
            else:
                shell_cmd = ["/bin/bash", "-c", cmd]

            logger.info(f"[sandbox:execute] Running: {cmd!r}")

            result = subprocess.run(
                shell_cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=self._cmd_timeout,
                env={
                    **os.environ,
                    # Prevent the child process from changing into directories
                    # outside the sandbox via environment tricks.
                    "HOME": str(self.root),
                    "USERPROFILE": str(self.root),
                },
            )

            elapsed = time.monotonic() - t0
            output_parts: List[str] = []

            if result.stdout.strip():
                output_parts.append(result.stdout.strip())
            if result.stderr.strip():
                output_parts.append(f"[stderr]\n{result.stderr.strip()}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            # Truncate very long output to avoid blowing up the LLM context.
            max_output = 8000
            if len(output) > max_output:
                output = output[:max_output] + f"\n… (truncated, {len(output):,} chars total)"

            status = "✅" if result.returncode == 0 else f"⚠️  exit code {result.returncode}"
            msg = f"{status} [{elapsed:.1f}s] $ {cmd}\n{output}"
            logger.info(
                f"[sandbox:execute] Finished (exit={result.returncode}, "
                f"{elapsed:.1f}s): {cmd!r}"
            )
            return msg

        except subprocess.TimeoutExpired:
            msg = (
                f"⏱️  Command timed out after {self._cmd_timeout}s: {cmd!r}"
            )
            logger.warning(f"[sandbox:execute] {msg}")
            return msg
        except SandboxSecurityError:
            raise
        except Exception as e:
            msg = f"❌ Command execution failed: {e}"
            logger.error(f"[sandbox:execute] {msg}", exc_info=True)
            return msg

    # ------------------------------------------------------------------
    # Tool definitions for LLM function calling
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return JSON-schema tool definitions suitable for passing to the LLM
        as available functions.

        The format follows the OpenAI/Gemini function-calling convention so
        the orchestrator can pass them directly to backends that support native
        tool use.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": (
                        "Create or overwrite a file inside the secure workspace. "
                        "The path is relative to the workspace root."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Relative file path within the workspace "
                                    "(e.g. 'src/main.py', 'README.md')."
                                ),
                            },
                            "content": {
                                "type": "string",
                                "description": "The full content to write to the file.",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": (
                        "Read the contents of a file inside the secure workspace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path within the workspace.",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": (
                        "List files and directories inside the secure workspace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Relative directory path within the workspace. "
                                    "Use '.' for the workspace root."
                                ),
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_command",
                    "description": (
                        "Run a shell command inside the secure workspace directory. "
                        "The working directory is always the workspace root. "
                        "Dangerous commands are blocked."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {
                                "type": "string",
                                "description": (
                                    "The shell command to execute "
                                    "(e.g. 'python main.py', 'ls -la', 'pip install requests')."
                                ),
                            },
                        },
                        "required": ["cmd"],
                    },
                },
            },
        ]

    def dispatch_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute a tool by name with the given arguments.

        This is the single entry-point the orchestrator uses to invoke tools.

        Parameters
        ----------
        tool_name
            One of ``write_file``, ``read_file``, ``list_files``,
            ``execute_command``.
        args
            Keyword arguments matching the tool's parameter schema.

        Returns
        -------
        str
            The tool's text result.

        Raises
        ------
        ValueError
            If *tool_name* is unknown.
        """
        dispatch_map = {
            "write_file": lambda a: self.write_file(a["path"], a["content"]),
            "read_file": lambda a: self.read_file(a["path"]),
            "list_files": lambda a: self.list_files(a.get("path", ".")),
            "execute_command": lambda a: self.execute_sandbox_command(a["cmd"]),
        }

        handler = dispatch_map.get(tool_name)
        if handler is None:
            raise ValueError(
                f"Unknown tool {tool_name!r}. "
                f"Available: {', '.join(dispatch_map.keys())}"
            )

        logger.info(f"[sandbox:dispatch] {tool_name}({args})")
        return handler(args)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict for diagnostics / HUD display."""
        file_count = sum(1 for _ in self.root.rglob("*") if _.is_file())
        total_size = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        return {
            "workspace_root": str(self.root),
            "file_count": file_count,
            "total_size_bytes": total_size,
            "max_read_bytes": self._max_read,
            "max_write_bytes": self._max_write,
            "command_timeout_s": self._cmd_timeout,
        }
