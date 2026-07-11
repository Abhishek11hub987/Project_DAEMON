"""
Multi-Agent Orchestrator — Pillar A
=====================================

Transforms DAEMON from a linear prompt/response chatbot into an autonomous
system that can **plan → code → test** complex tasks using three specialised
agent personas.

Architecture
------------
::

    User Task
        │
        ▼
    ┌─────────────┐   structured plan    ┌─────────────┐   code files
    │  ARCHITECT   │ ──────────────────▶  │  DEVELOPER   │ ──────────▶
    └─────────────┘                      └─────────────┘
                                                │
                                                ▼
                                          ┌──────────┐
                                          │    QA     │ ─── test & fix ──▶ Done
                                          └──────────┘

Each agent:
    1. Receives a persona-specific system prompt.
    2. Sees the accumulated ``TaskContext`` (plan, files written, errors).
    3. Can invoke workspace tools via function-calling or JSON blocks.
    4. Broadcasts progress events to the WebSocket HUD.

The orchestrator re-uses the existing ``LLMEngine`` and ``WorkspaceSandbox``
— no new external dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core_logic.config import Config
from core_logic.error_handler import OrchestratorException
from core_logic.llm_engine import LLMEngine
from skills.workspace import WorkspaceSandbox

logger = logging.getLogger(__name__)


# =========================================================================
# Agent personas
# =========================================================================

class AgentRole(str, Enum):
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    QA = "qa_engineer"


@dataclass
class AgentPersona:
    """Lightweight description of an agent persona.

    Each persona carries its own system prompt, the list of tools it is
    allowed to use, and a cap on how many LLM turns it may take.
    """

    name: str
    role: AgentRole
    system_prompt: str
    available_tools: List[str]
    max_iterations: int = 5


# Pre-built personas -------------------------------------------------------

_ARCHITECT_PROMPT = """\
You are the **Architect** agent inside the D.A.E.M.O.N. system.

Your job: given a user task, produce a clear, structured build plan.

OUTPUT FORMAT — you MUST respond with ONLY valid JSON, no markdown fences, no commentary:
{
  "plan_summary": "Brief one-sentence summary of what will be built",
  "files": [
    {
      "path": "relative/file/path.ext",
      "purpose": "What this file does",
      "dependencies": ["any", "pip", "packages"]
    }
  ],
  "build_steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "test_strategy": "How to verify the output works"
}

Rules:
- File paths are RELATIVE to the workspace root.
- Keep the plan minimal and actionable — no essays.
- Include a clear test_strategy the QA agent can follow.
- Do NOT write any code — the Developer agent handles that.
"""

_DEVELOPER_PROMPT = """\
You are the **Developer** agent inside the D.A.E.M.O.N. system.

You receive a build plan from the Architect. Your job: write every file
listed in the plan by calling the workspace tools.

TOOLS AVAILABLE:
- write_file(path, content)  — create/overwrite a file
- read_file(path)            — read a file's contents
- list_files(path)           — list workspace contents

HOW TO CALL TOOLS — emit one or more tool-call blocks (one per line):
```tool_call
{"tool": "write_file", "args": {"path": "src/main.py", "content": "print('hello')"}}
```

Rules:
- Write COMPLETE, PRODUCTION-QUALITY code — no placeholders or TODOs.
- Include proper imports, error handling, and docstrings.
- One tool_call block per file. You may emit multiple blocks in a single response.
- After writing all files, end your response with: DONE
- If you need to read an existing file first, call read_file before writing.
"""

_QA_PROMPT = """\
You are the **QA Engineer** agent inside the D.A.E.M.O.N. system.

You receive the Architect's plan and the list of files the Developer wrote.
Your job: verify correctness by running commands and reading output.

TOOLS AVAILABLE:
- execute_command(cmd)  — run a shell command in the workspace
- read_file(path)       — read a file's contents

HOW TO CALL TOOLS — emit tool-call blocks:
```tool_call
{"tool": "execute_command", "args": {"cmd": "python main.py"}}
```

Workflow:
1. Read the key files to spot obvious issues (syntax, imports, logic).
2. Run the test strategy from the plan (e.g. execute the program).
3. If tests PASS → respond with: ✅ ALL TESTS PASSED
4. If tests FAIL → explain the issue, suggest a fix (you may call
   write_file to patch small problems), then re-test.
5. Max fix attempts: 3. After that, summarise remaining issues.
"""

AGENT_PERSONAS: Dict[AgentRole, AgentPersona] = {
    AgentRole.ARCHITECT: AgentPersona(
        name="Architect",
        role=AgentRole.ARCHITECT,
        system_prompt=_ARCHITECT_PROMPT,
        available_tools=[],  # planning only — no tool access
        max_iterations=1,
    ),
    AgentRole.DEVELOPER: AgentPersona(
        name="Developer",
        role=AgentRole.DEVELOPER,
        system_prompt=_DEVELOPER_PROMPT,
        available_tools=["write_file", "read_file", "list_files"],
        max_iterations=10,
    ),
    AgentRole.QA: AgentPersona(
        name="QA Engineer",
        role=AgentRole.QA,
        system_prompt=_QA_PROMPT,
        available_tools=["execute_command", "read_file", "write_file"],
        max_iterations=5,
    ),
}


# =========================================================================
# Task context — accumulated state across agents
# =========================================================================

@dataclass
class TaskContext:
    """Accumulates the full execution trace across all agents.

    Passed from Architect → Developer → QA so each agent has full
    visibility into what previous agents did.
    """

    task_description: str
    plan: Optional[str] = None
    plan_json: Optional[Dict[str, Any]] = None
    files_written: List[str] = field(default_factory=list)
    execution_logs: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    status: str = "pending"
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    total_llm_calls: int = 0
    total_tool_calls: int = 0

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    def as_context_block(self) -> str:
        """Serialise the context into a text block the LLM can consume."""
        parts = [f"## TASK\n{self.task_description}"]
        if self.plan:
            parts.append(f"## ARCHITECT PLAN\n{self.plan}")
        if self.files_written:
            parts.append("## FILES WRITTEN\n" + "\n".join(
                f"- {f}" for f in self.files_written
            ))
        if self.execution_logs:
            parts.append("## EXECUTION LOG")
            for entry in self.execution_logs[-10:]:  # keep last 10 to fit context
                parts.append(
                    f"[{entry['agent']}] {entry['tool']}: {entry['result'][:500]}"
                )
        if self.errors:
            parts.append("## ERRORS\n" + "\n".join(self.errors[-5:]))
        return "\n\n".join(parts)

    def to_summary(self) -> Dict[str, Any]:
        """Return a JSON-safe summary for the HUD / voice readout."""
        return {
            "task": self.task_description,
            "status": self.status,
            "files_written": self.files_written,
            "error_count": len(self.errors),
            "llm_calls": self.total_llm_calls,
            "tool_calls": self.total_tool_calls,
            "elapsed_seconds": round(self.elapsed, 1),
        }


# =========================================================================
# Tool-call parser
# =========================================================================

# Matches ```tool_call\n{...}\n``` blocks emitted by the Developer / QA
_TOOL_CALL_PATTERN = re.compile(
    r"```tool_call\s*\n\s*(\{.*?\})\s*\n\s*```",
    re.DOTALL,
)

# Fallback: bare JSON objects with "tool" and "args" keys (no fences)
_BARE_JSON_PATTERN = re.compile(
    r'\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"args"\s*:\s*(\{.*?\})\s*\}',
    re.DOTALL,
)


def parse_tool_calls(response: str) -> List[Dict[str, Any]]:
    """Extract tool-call instructions from an LLM response.

    Supports two formats:

    1. Fenced blocks::

           ```tool_call
           {"tool": "write_file", "args": {"path": "x.py", "content": "..."}}
           ```

    2. Bare JSON objects with ``tool`` and ``args`` keys.

    Returns a list of ``{"tool": str, "args": dict}`` dicts.
    """
    calls: List[Dict[str, Any]] = []

    # Strategy 1: fenced blocks
    for match in _TOOL_CALL_PATTERN.finditer(response):
        raw = match.group(1)
        try:
            obj = json.loads(raw)
            if "tool" in obj and "args" in obj:
                calls.append({"tool": obj["tool"], "args": obj["args"]})
        except json.JSONDecodeError:
            logger.warning(f"[parser] Invalid JSON in tool_call block: {raw[:120]}")

    if calls:
        return calls

    # Strategy 2: bare JSON fallback
    for match in _BARE_JSON_PATTERN.finditer(response):
        tool_name = match.group(1)
        args_raw = match.group(2)
        try:
            args = json.loads(args_raw)
            calls.append({"tool": tool_name, "args": args})
        except json.JSONDecodeError:
            logger.warning(f"[parser] Invalid JSON in bare tool call: {args_raw[:120]}")

    return calls


# =========================================================================
# Orchestrator
# =========================================================================

class Orchestrator:
    """Multi-agent task orchestration engine.

    Binds an ``LLMEngine`` to a ``WorkspaceSandbox`` and runs the
    Architect → Developer → QA pipeline asynchronously.

    Parameters
    ----------
    llm
        The LLM backend to use for all agent calls.
    sandbox
        The workspace sandbox for tool execution.
    event_callback
        Optional callable ``(event: dict) -> None`` invoked on every
        significant state change.  Designed to feed the WebSocket
        ``EventBus.publish_threadsafe``.
    """

    def __init__(
        self,
        llm: LLMEngine,
        sandbox: WorkspaceSandbox,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.llm = llm
        self.sandbox = sandbox
        self._emit = event_callback or (lambda e: None)
        self._max_iterations = Config.ORCHESTRATOR_MAX_TOTAL_ITERATIONS

        # Cancellation flag — can be set from any thread (e.g. WebSocket HUD)
        # to gracefully abort the current task at the next iteration boundary.
        self._cancel_event = asyncio.Event()

        logger.info(
            f"🧠 Orchestrator initialised — backend: {llm.backend}, "
            f"max iterations: {self._max_iterations}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_task(self, task_description: str) -> TaskContext:
        """Run the full Architect → Developer → QA pipeline.

        This method is async so it can yield control back to the event loop
        between LLM calls, keeping the FastAPI server responsive.

        The loop checks ``self._cancel_event`` at every agent transition.
        Call :meth:`cancel` from any thread (e.g. WebSocket HUD button) to
        request a graceful abort.

        Parameters
        ----------
        task_description
            Free-text description of what the user wants built.

        Returns
        -------
        TaskContext
            The accumulated execution trace with all files, logs, and errors.
        """
        # Reset the cancel flag so the orchestrator is reusable across runs.
        self._cancel_event.clear()

        ctx = TaskContext(task_description=task_description)
        ctx.status = "running"

        self._emit({
            "type": "orchestrator_start",
            "task": task_description,
        })

        pipeline = [
            AgentRole.ARCHITECT,
            AgentRole.DEVELOPER,
            AgentRole.QA,
        ]

        try:
            for role in pipeline:
                # ---- Cancellation check between agents ----
                if self._cancel_event.is_set():
                    logger.info("🛑 Orchestrator cancelled by user.")
                    ctx.status = "cancelled"
                    ctx.errors.append("Task cancelled by user.")
                    self._emit({"type": "orchestrator_cancelled"})
                    break

                agent = AGENT_PERSONAS[role]
                await self._run_agent(agent, ctx)

                # Global iteration guard
                if ctx.total_llm_calls >= self._max_iterations:
                    logger.warning(
                        f"Global iteration limit ({self._max_iterations}) "
                        f"reached — stopping orchestrator."
                    )
                    ctx.errors.append(
                        f"Hit global iteration limit ({self._max_iterations}). "
                        f"Task may be incomplete."
                    )
                    break

            # Only mark completed if we weren't cancelled or iteration-capped.
            if ctx.status == "running":
                ctx.status = "completed"

        except OrchestratorException:
            ctx.status = "failed"
            raise
        except Exception as e:
            ctx.status = "failed"
            ctx.errors.append(f"Orchestrator crash: {e}")
            logger.error(f"Orchestrator failed: {e}", exc_info=True)
            raise OrchestratorException(f"Orchestrator failed: {e}") from e
        finally:
            ctx.finished_at = time.time()
            self._emit({
                "type": "orchestrator_done",
                "status": ctx.status,
                "summary": ctx.to_summary(),
            })

        return ctx

    # ------------------------------------------------------------------
    # Internal: single-agent loop
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        agent: AgentPersona,
        ctx: TaskContext,
    ) -> None:
        """Run a single agent's iterative loop.

        The agent keeps calling the LLM until one of:
        - It emits no tool calls (conversation complete).
        - It outputs ``DONE`` or ``ALL TESTS PASSED``.
        - It hits its per-agent iteration cap.
        - The global iteration cap is reached.
        """
        logger.info(f"═══ Agent [{agent.name}] starting ═══")
        self._emit({
            "type": "agent_start",
            "agent": agent.name,
            "role": agent.role.value,
        })

        # Build the user message with accumulated context
        user_message = ctx.as_context_block()

        # Tool definitions for agents that have tool access
        tool_defs = self.sandbox.get_tool_definitions()
        # Filter to only the tools this agent is allowed to use
        allowed_tools = [
            t for t in tool_defs
            if t["function"]["name"] in agent.available_tools
        ]

        # Conversation buffer for multi-turn within this agent
        conversation: List[Dict[str, str]] = []

        for iteration in range(1, agent.max_iterations + 1):
            # ---- Cancellation + iteration guards ----
            if self._cancel_event.is_set():
                logger.info(f"  [{agent.name}] Cancelled by user.")
                break
            if ctx.total_llm_calls >= self._max_iterations:
                break

            logger.info(
                f"  [{agent.name}] iteration {iteration}/{agent.max_iterations}"
            )
            self._emit({
                "type": "agent_iteration",
                "agent": agent.name,
                "iteration": iteration,
                "max_iterations": agent.max_iterations,
            })

            # ---- Build the prompt ----
            if iteration == 1:
                prompt = user_message
                if allowed_tools:
                    tool_list = ", ".join(
                        t["function"]["name"] for t in allowed_tools
                    )
                    prompt += (
                        f"\n\n## AVAILABLE TOOLS\n"
                        f"You may use: {tool_list}\n"
                        f"Emit tool calls in ```tool_call``` fenced blocks."
                    )
            else:
                # On subsequent iterations the prompt is built from the
                # tool results accumulated in the conversation buffer.
                prompt = conversation[-1]["content"] if conversation else ""

            # ---- Call the LLM (offloaded to thread to stay async) ----
            try:
                response = await asyncio.to_thread(
                    self.llm.generate,
                    prompt=prompt,
                    temperature=0.3,  # lower temp for deterministic code gen
                    max_tokens=4000,
                    system_prompt=agent.system_prompt,
                    context=conversation[:-1] if len(conversation) > 1 else None,
                )
                ctx.total_llm_calls += 1
            except Exception as e:
                error_msg = f"LLM call failed in [{agent.name}]: {e}"
                ctx.errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
                self._emit({
                    "type": "agent_error",
                    "agent": agent.name,
                    "error": str(e),
                })
                break

            if not response or not isinstance(response, str):
                logger.warning(f"  [{agent.name}] Empty response from LLM")
                break

            # Record in conversation buffer
            conversation.append({"role": "user", "content": prompt})
            conversation.append({"role": "assistant", "content": response})

            self._emit({
                "type": "agent_response",
                "agent": agent.name,
                "iteration": iteration,
                "response_preview": response[:300],
            })

            # ---- Architect: parse plan from JSON response ----
            if agent.role == AgentRole.ARCHITECT:
                ctx.plan = response
                try:
                    # Strip markdown fences if the model wrapped it
                    clean = response.strip()
                    if clean.startswith("```"):
                        # Remove opening/closing fences
                        clean = re.sub(r"^```\w*\n?", "", clean)
                        clean = re.sub(r"\n?```\s*$", "", clean)
                    ctx.plan_json = json.loads(clean)
                    logger.info(
                        f"  [Architect] Plan parsed: "
                        f"{len(ctx.plan_json.get('files', []))} files"
                    )
                except json.JSONDecodeError:
                    logger.warning(
                        "  [Architect] Could not parse JSON plan — "
                        "Developer will work from raw text."
                    )
                break  # Architect always runs exactly once

            # ---- Developer / QA: parse and execute tool calls ----
            tool_calls = parse_tool_calls(response)

            if tool_calls:
                tool_results: List[str] = []
                for tc in tool_calls:
                    tool_name = tc["tool"]
                    tool_args = tc["args"]

                    # Verify the agent has access to this tool
                    if tool_name not in agent.available_tools:
                        result = (
                            f"⛔ Tool {tool_name!r} is not available to the "
                            f"{agent.name} agent."
                        )
                        logger.warning(f"  [{agent.name}] {result}")
                    else:
                        try:
                            result = self.sandbox.dispatch_tool(tool_name, tool_args)
                            ctx.total_tool_calls += 1

                            # Track files written
                            if tool_name == "write_file" and "path" in tool_args:
                                path = tool_args["path"]
                                if path not in ctx.files_written:
                                    ctx.files_written.append(path)

                        except Exception as e:
                            result = f"❌ Tool error: {e}"
                            ctx.errors.append(f"[{agent.name}] {tool_name}: {e}")
                            logger.error(
                                f"  [{agent.name}] Tool {tool_name} failed: {e}",
                                exc_info=True,
                            )

                    ctx.execution_logs.append({
                        "agent": agent.name,
                        "tool": tool_name,
                        "args": json.dumps(tool_args, default=str)[:200],
                        "result": result[:500],
                        "timestamp": time.time(),
                    })

                    tool_results.append(
                        f"Tool `{tool_name}` result:\n{result}"
                    )

                    self._emit({
                        "type": "tool_result",
                        "agent": agent.name,
                        "tool": tool_name,
                        "result_preview": result[:200],
                    })

                # Feed tool results back as the next user message
                conversation.append({
                    "role": "user",
                    "content": (
                        "Tool results:\n\n"
                        + "\n\n---\n\n".join(tool_results)
                        + "\n\nContinue with the next step, or say DONE."
                    ),
                })
            else:
                # No tool calls — check for completion signals
                response_upper = response.upper()
                if "DONE" in response_upper or "ALL TESTS PASSED" in response_upper:
                    logger.info(f"  [{agent.name}] ✅ Signalled completion.")
                    break

                # If no tool calls and no completion signal on a tool-using
                # agent, nudge it once — then break to avoid infinite loops.
                if iteration < agent.max_iterations and agent.available_tools:
                    conversation.append({
                        "role": "user",
                        "content": (
                            "You didn't call any tools. Please use the "
                            "available tools to complete your task, or "
                            "say DONE if you're finished."
                        ),
                    })
                else:
                    break

        logger.info(f"═══ Agent [{agent.name}] finished ═══")
        self._emit({
            "type": "agent_done",
            "agent": agent.name,
            "files_written": ctx.files_written.copy(),
            "error_count": len(ctx.errors),
        })

    # ------------------------------------------------------------------
    # Convenience: synchronous entry point for non-async callers
    # ------------------------------------------------------------------

    def run_task_sync(self, task_description: str) -> TaskContext:
        """Blocking wrapper around :meth:`run_task`.

        Uses ``asyncio.run()`` so it works from the DAEMON text-mode
        REPL or unit tests without an existing event loop.
        """
        return asyncio.run(self.run_task(task_description))

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Request graceful cancellation of the running task.

        Thread-safe — can be called from the WebSocket HUD handler, a
        hotkey callback, or any other thread.  The async loop will pick
        up the flag at the next iteration boundary and wind down cleanly.
        """
        logger.info("🛑 Orchestrator cancel requested.")
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check whether a cancellation has been requested."""
        return self._cancel_event.is_set()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return orchestrator metadata for the HUD."""
        return {
            "llm_backend": self.llm.backend,
            "max_iterations": self._max_iterations,
            "cancelled": self.is_cancelled,
            "workspace": self.sandbox.get_status(),
        }
