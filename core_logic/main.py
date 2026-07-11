"""
D.A.E.M.O.N. Main Entry Point - Phase 6

Complete audio loop with memory, skill routing, multi-agent personas,
and proactive state management:
Wake Word → Listen → Transcribe → Route (Agents/Skills/LLM) → Respond
"""

import asyncio
import logging
import re
import sys
import threading
from typing import Optional

from audio.audio_pipeline import AudioPipeline
from core_logic.config import Config
from core_logic.llm_engine import LLMEngine
from core_logic.memory import ConversationMemory, MultiUserMemory
from core_logic.session_manager import SessionManager
from core_logic.skill_router import SkillRouter
from utils.logger import setup_logging
from core_logic.error_handler import handle_exception
from core_logic.skill_router import SkillType

logger = logging.getLogger(__name__)

# Lazy imports for Pillar A — avoids hard dependency if orchestrator
# modules haven't been created yet.
try:
    from core_logic.orchestrator import Orchestrator, TaskContext
    from skills.workspace import WorkspaceSandbox
    _ORCHESTRATOR_AVAILABLE = True
except ImportError:
    _ORCHESTRATOR_AVAILABLE = False

# Lazy imports for Pillar C — state store, background worker, briefing
try:
    from core_logic.state_store import UnifiedStateStore
    from core_logic.background_worker import ProactiveMonitor
    from core_logic.briefing_engine import BriefingEngine
    _BRIEFING_AVAILABLE = True
except ImportError:
    _BRIEFING_AVAILABLE = False

# Lazy imports for Phase 6 — multi-agent state machine + registry
try:
    from core_logic.state_manager import StateManager, DaemonState
    from core_logic.agent_registry import AgentRegistry
    _AGENTS_AVAILABLE = True
except ImportError:
    _AGENTS_AVAILABLE = False

# Lazy imports for Phase 7 — RAG pipeline
try:
    from core_logic.rag_engine import RAGEngine
    _RAG_AVAILABLE = True
except ImportError:
    _RAG_AVAILABLE = False


class DAEMON:
    """
    Main D.A.E.M.O.N. orchestrator.
    
    Coordinates:
    - Audio I/O (wake word, STT, TTS)
    - LLM processing with memory
    - Skill routing and execution (Phase 3-4)
    - Multi-agent persona routing with voice swapping (Phase 6)
    - Proactive state management (ACTIVE/AWAY/BRIEFING)
    - Multi-turn conversations
    - Document processing, system monitoring, file management, C integration
    """
    
    def __init__(self, user_id: str = "default"):
        """Initialize D.A.E.M.O.N."""
        self.config = Config
        self.logger = setup_logging()
        self.is_running = False
        self.user_id = user_id
        
        logger.info("🤖 D.A.E.M.O.N. Phase 6 Initialization")
        
        # Initialize components
        try:
            self.audio = AudioPipeline()
            logger.info("✅ Audio pipeline initialized")
        except Exception as e:
            logger.error(f"Audio pipeline failed: {str(e)}")
            self.audio = None
        
        try:
            self.llm = LLMEngine()
            logger.info("✅ LLM engine initialized")
        except Exception as e:
            logger.warning(f"LLM engine not available: {str(e)}")
            self.llm = None
        
        try:
            # Initialize memory and skill router (Phase 3-4)
            self.memory_manager = MultiUserMemory()
            self.memory = self.memory_manager.get_conversation(user_id)
            self.skill_router = SkillRouter()  # Now includes Phase 4 skills
            logger.info("✅ Memory and skill router initialized (Phase 3 + Phase 4)")
        except Exception as e:
            logger.warning(f"Memory/routing not available: {str(e)}")
            self.memory = None
            self.skill_router = None

        # Conversation session manager — one persisted file per session, used
        # by the web UI sidebar.
        try:
            self.sessions = SessionManager()
        except Exception as e:
            logger.warning(f"Session manager not available: {str(e)}")
            self.sessions = None

        # Wire pipeline session events → SessionManager so the web UI
        # automatically reflects voice-driven conversations.
        if self.audio is not None and self.sessions is not None:
            self.audio.on_session_start = self._on_pipeline_session_start
            self.audio.on_session_end = self._on_pipeline_session_end
            self.audio.on_user_message = self._on_pipeline_user_message
            self.audio.on_assistant_message = self._on_pipeline_assistant_message

        # Pillar A: Multi-Agent Orchestrator (optional)
        self.orchestrator = None
        if _ORCHESTRATOR_AVAILABLE and self.llm:
            try:
                sandbox = WorkspaceSandbox()
                self.orchestrator = Orchestrator(
                    llm=self.llm,
                    sandbox=sandbox,
                )
                logger.info("✅ Orchestrator initialised (Architect → Developer → QA)")
            except Exception as e:
                logger.warning(f"Orchestrator not available: {e}")

        # Pillar C: State Store + Background Worker + Briefing Engine
        self.state_store = None
        self.background_worker = None
        self.briefing_engine = None
        if _BRIEFING_AVAILABLE:
            try:
                self.state_store = UnifiedStateStore()
                self.background_worker = ProactiveMonitor(
                    state_store=self.state_store,
                )
                self.briefing_engine = BriefingEngine(
                    state_store=self.state_store,
                    llm=self.llm,
                    background_worker=self.background_worker,
                )
                logger.info("✅ State Store + Background Worker + Briefing Engine ready")
            except Exception as e:
                logger.warning(f"Briefing subsystem not available: {e}")

        # Phase 6: State Manager + Agent Registry
        self.state_manager = None
        self.agent_registry = None
        if _AGENTS_AVAILABLE:
            try:
                self.state_manager = StateManager(
                    on_enter_active=self._on_enter_active,
                    on_enter_away=self._on_enter_away,
                    on_enter_briefing=self._on_enter_briefing,
                )
                self.agent_registry = AgentRegistry()
                logger.info(
                    f"✅ Phase 6 ready — StateManager + "
                    f"{len(self.agent_registry)} agent(s): "
                    f"{', '.join(self.agent_registry.agent_names) or '(none)'}"
                )
            except Exception as e:
                logger.warning(f"Phase 6 subsystem not available: {e}")

        # Phase 7: RAG pipeline
        self.rag_engine = None
        if _RAG_AVAILABLE and Config.ENABLE_RAG:
            try:
                self.rag_engine = RAGEngine()
                # Index in background so startup isn't blocked
                self.rag_engine.ingest_background()
                logger.info("✅ RAG engine initialised (indexing in background)")
            except Exception as e:
                logger.warning(f"RAG engine not available: {e}")

        logger.info("🤖 D.A.E.M.O.N. Phase 7 ready")

    # ---------- pipeline session bridge -----------------------------------
    def _on_pipeline_session_start(self) -> None:
        if self.sessions:
            self.sessions.begin()
        # Start a fresh in-memory turn buffer for this session so the LLM only
        # sees the current conversation's context — past sessions remain on
        # disk and can be re-loaded from the UI.
        if self.memory:
            self.memory.clear()

    def _on_pipeline_session_end(self, reason: str) -> None:
        if self.sessions:
            self.sessions.end(reason)

    def _on_pipeline_user_message(self, text: str) -> None:
        if self.sessions:
            self.sessions.add_turn("user", text)

    def _on_pipeline_assistant_message(self, text: str) -> None:
        if self.sessions:
            self.sessions.add_turn("assistant", text)

    # ---------- Phase 6 state callbacks ------------------------------------
    def _on_enter_active(self) -> None:
        """Called when transitioning to ACTIVE mode."""
        logger.info("🎤 Entering ACTIVE mode — microphone live")
        if self.audio:
            self.audio.is_listening = True

    def _on_enter_away(self) -> None:
        """Called when transitioning to AWAY mode.

        Sleeps the microphone and dispatches background agent tasks.
        """
        logger.info("💤 Entering AWAY mode — microphone asleep, agents working")
        if self.audio:
            self.audio.is_listening = False
            mic = getattr(self.audio, "microphone", None)
            if mic and hasattr(mic, "stop_recording") and mic.is_recording:
                try:
                    mic.stop_recording()
                except Exception:
                    pass

        # Dispatch background tasks for all agents
        if self.agent_registry:
            import asyncio as _aio
            async def _dispatch():
                results = await self.agent_registry.run_all_background_tasks()
                if self.state_manager:
                    for entry in results:
                        self.state_manager.append_to_briefing_queue(entry)
                logger.info(f"📝 {len(results)} agent report(s) queued for briefing")

            try:
                loop = _aio.get_running_loop()
                _aio.ensure_future(_dispatch())
            except RuntimeError:
                _aio.run(_dispatch())

    def _on_enter_briefing(self) -> None:
        """Called when transitioning to BRIEFING mode.

        Consumes the briefing queue, sends it to the LLM for narrative
        synthesis, and speaks the result.
        """
        logger.info("📢 Entering BRIEFING mode — synthesising narrative")
        if not self.state_manager:
            return

        queue = self.state_manager.consume_briefing_queue()
        if not queue:
            if self.audio:
                self.audio.speak("Nothing to report. All quiet while you were away.")
            # Transition directly to ACTIVE
            self.state_manager.go_active()
            return

        # Build a data block for the LLM
        import json as _json
        data_block = _json.dumps(queue, indent=2, default=str)

        briefing_text = None
        if self.llm:
            prompt = (
                f"Here are {len(queue)} background reports collected while "
                f"the user was away:\n\n{data_block}\n\n"
                f"Synthesise these into a flowing, spoken briefing. "
                f"Mention each agent by name. Keep it under 120 words."
            )
            try:
                briefing_text = self.llm.generate(
                    prompt=prompt,
                    temperature=0.6,
                    max_tokens=300,
                )
            except Exception as exc:
                logger.error(f"Briefing synthesis failed: {exc}")

        if not briefing_text:
            # Fallback: concatenate summaries
            summaries = [e.get("summary", "") for e in queue if e.get("summary")]
            briefing_text = " ".join(summaries) if summaries else "Reports collected but summary generation failed."

        if self.audio:
            self.audio.speak(briefing_text)

        # Return to ACTIVE after briefing
        self.state_manager.go_active()
    
    @staticmethod
    def _jarvisify(text: str) -> str:
        """
        Light-touch personality pass on raw skill responses.

        We deliberately do NOT add 'Yes, sir... done, sir.' to every reply any
        more — that read as robotic when spoken aloud. The natural personality
        comes from the system prompt and from each skill's own phrasing.
        Here we just trim whitespace and drop any double newlines.
        """
        if not text:
            return text
        body = text.strip()
        # Collapse stray blank lines so TTS doesn't pause forever between them.
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body

    def process_command(self, text: str) -> str:
        """
        Process user command through skills and/or LLM.
        
        Args:
            text: User input text
            
        Returns:
            Response text
        """
        t_lower = text.strip().lower()
        
        # Intercept slash commands so they work from both Web UI and Terminal
        if t_lower == "/away":
            if not getattr(self, "state_manager", None): return "State manager not available."
            if self.state_manager.go_away(): return "Entering AWAY mode. Background agents dispatched."
            return f"Cannot transition to AWAY from {self.state_manager.state.value}."
            
        if t_lower == "/active":
            if not getattr(self, "state_manager", None): return "State manager not available."
            if self.state_manager.go_active(): return "Back to ACTIVE mode."
            return f"Cannot transition to ACTIVE from {self.state_manager.state.value}."
            
        if t_lower == "/briefing":
            if not getattr(self, "state_manager", None): return "State manager not available."
            if self.state_manager.start_briefing(): return "Generating multi-agent briefing..."
            return f"Cannot transition to BRIEFING from {self.state_manager.state.value}."
            
        if t_lower == "/agents":
            if not getattr(self, "agent_registry", None): return "Agent registry not available."
            agents = self.agent_registry.list_agents()
            if not agents: return "No agents registered."
            lines = [f"Registered agents ({len(agents)}):"]
            for a in agents: lines.append(f" - {a['display_name']} ({a['voice_model']})")
            return "\n".join(lines)
            
        if t_lower == "/poll":
            if not getattr(self, "background_worker", None): return "Background worker not available."
            try:
                res = self.background_worker.poll_now_sync()
                return f"Poll complete. Email: {res.get('email', {}).get('unread_count', 0)} unread. GitHub: {res.get('github', {}).get('open_pr_count', 0)} PRs."
            except Exception as e:
                return f"Poll error: {e}"
                
        if t_lower == "/status":
            if getattr(self, "rag_engine", None):
                stats = self.rag_engine.get_stats()
                return f"System running. RAG: {stats.get('chunks', 0)} chunks indexed. State: {getattr(self.state_manager, 'state', 'N/A')}."
            return "System is running normally."

        if t_lower == "/index":
            if not getattr(self, "rag_engine", None): return "RAG engine not available."
            try:
                summary = self.rag_engine.ingest_all()
                total = sum(summary.values())
                return f"Re-indexing complete. {total} new chunks added across {len(summary)} directories."
            except Exception as e:
                return f"Indexing failed: {e}"

        if t_lower in ("/send", "/confirm", "yes send it", "yes", "send it"):
            try:
                from skills.messaging_skill import MessagingSkill
                if MessagingSkill._pending:
                    return MessagingSkill.confirm_send()
            except Exception:
                pass
            # Fall through to LLM if no pending message

        if t_lower == "/contacts":
            try:
                from skills.messaging_skill import MessagingSkill
                return MessagingSkill.list_contacts()
            except Exception as e:
                return f"Contacts not available: {e}"

        # Snapshot prior conversation BEFORE adding the current user turn —
        # otherwise the LLM call would see the current message twice (once in
        # the context list, once as the explicit prompt argument).
        prior_context = self.memory.get_context() if self.memory else None

        # Phase 7: Augment context with RAG retrieval
        if getattr(self, "rag_engine", None):
            try:
                prior_context = self.rag_engine.augment_prompt(text, prior_context)
            except Exception as e:
                logger.warning(f"RAG augmentation failed: {e}")

        # Now record this user turn for future cycles / persistence.
        if self.memory:
            self.memory.add_turn("user", text)

        # Try skill routing first
        if self.skill_router:
            try:
                # Check for BUILD tasks → route to orchestrator
                skill_type = self.skill_router.classify_command(text)
                if skill_type == SkillType.BUILD and self.orchestrator:
                    return self._run_orchestrator_task(text)

                # Check for WEB_BUILD → use WebBuilderSkill + Orchestrator
                if skill_type == SkillType.WEB_BUILD and self.orchestrator:
                    from skills.web_builder_skill import WebBuilderSkill
                    task_desc = WebBuilderSkill.handle(text)
                    response = self._run_orchestrator_task(task_desc)
                    # Try to auto-open the result
                    try:
                        open_msg = WebBuilderSkill.open_result()
                        response += f" {open_msg}"
                    except Exception:
                        pass
                    return response

                # Check for MESSAGING → route to messaging skill
                if skill_type == SkillType.MESSAGING:
                    from skills.messaging_skill import MessagingSkill
                    return MessagingSkill.handle(text)

                # Check for BRIEFING tasks → route to briefing engine
                if skill_type == SkillType.BRIEFING and self.briefing_engine:
                    return self._run_briefing(text)

                # Check for AGENT tasks → route to persona agent
                if skill_type == SkillType.AGENT and self.agent_registry:
                    return self._run_agent_query(text)

                skill_response = self.skill_router.execute_command(text)
                if skill_response:
                    skill_response = self._jarvisify(skill_response)
                    if self.memory:
                        self.memory.add_turn("assistant", skill_response)
                    logger.info("✅ Routed to skill")
                    return skill_response
            except Exception as e:
                logger.warning(f"Skill execution failed: {str(e)}")

        # Fall back to LLM
        if not self.llm:
            return self._jarvisify(f"I heard: {text}")

        try:
            logger.info(f"📝 Processing with LLM: {text}")
            response = self.llm.generate(
                prompt=text,
                temperature=0.7,
                max_tokens=512,
                context=prior_context,
            )

            # Phase 7: Interactivity Polish
            # If the response feels conclusive (ends with a period, not a question),
            # occasionally append a follow-up nudge to keep the conversation flowing.
            if response and response.strip().endswith("."):
                import random
                if random.random() < 0.25:  # 25% chance
                    nudges = [
                        " Make sense?",
                        " You know what I mean?",
                        " Want me to look into that a bit more?",
                        " Sound good?",
                        " Need me to do anything else with this?",
                        " Let me know if you want to dig deeper into that.",
                        " Make sense, or should I explain it differently?",
                    ]
                    response += random.choice(nudges)

            if self.memory:
                self.memory.add_turn("assistant", response)
            return response
        except Exception as e:
            logger.error(f"Command processing failed: {str(e)}")
            return handle_exception(e, "Command processing")

    # ---------- Orchestrator integration -----------------------------------

    def _run_orchestrator_task(self, task_description: str) -> str:
        """Run a multi-agent build task via the orchestrator.

        This bridges the synchronous ``process_command()`` call to the
        async orchestrator pipeline.  If an event loop is already running
        (e.g. FastAPI), the task is submitted to it; otherwise we spin up
        a temporary loop.

        Returns a human-readable summary suitable for TTS.
        """
        logger.info(f"🧠 Routing to orchestrator: {task_description}")

        try:
            # Try to use the running loop (FastAPI / web mode)
            loop = asyncio.get_running_loop()
            # We're inside an async context — can't block. Run in a thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                ctx = pool.submit(
                    asyncio.run, self.orchestrator.run_task(task_description)
                ).result(timeout=300)  # 5-minute hard timeout
        except RuntimeError:
            # No event loop running — text mode or direct call
            ctx = self.orchestrator.run_task_sync(task_description)

        # Build a TTS-friendly summary
        return self._format_orchestrator_result(ctx)

    @staticmethod
    def _format_orchestrator_result(ctx: "TaskContext") -> str:
        """Format orchestrator results for voice output."""
        summary = ctx.to_summary()
        files = summary.get("files_written", [])
        errors = summary.get("error_count", 0)
        elapsed = summary.get("elapsed_seconds", 0)
        status = summary.get("status", "unknown")

        parts = []
        if status == "completed":
            parts.append("Task completed successfully.")
        elif status == "cancelled":
            parts.append("Task was cancelled.")
        else:
            parts.append(f"Task finished with status: {status}.")

        if files:
            parts.append(f"I wrote {len(files)} file{'s' if len(files) != 1 else ''}: {', '.join(files)}.")

        if errors:
            parts.append(f"There {'were' if errors != 1 else 'was'} {errors} error{'s' if errors != 1 else ''} during execution.")

        parts.append(f"Total time: {elapsed} seconds.")

        return " ".join(parts)

    # ---------- Briefing integration ----------------------------------------

    def _run_briefing(self, text: str) -> str:
        """Generate a J.A.R.V.I.S.-style status briefing.

        Triggers a fresh poll of email/GitHub sensors, then passes the
        latest state to the LLM for a narrative summary.
        """
        logger.info(f"🎤 Routing to briefing engine: {text}")

        try:
            briefing = self.briefing_engine.compile_vocal_briefing(
                fresh_poll=True,
            )
            if self.memory:
                self.memory.add_turn("assistant", briefing)
            return briefing
        except Exception as e:
            logger.error(f"Briefing failed: {e}")
            return "I had trouble pulling together the status update. I'll try again in a moment."

    # ---------- Phase 6: Agent query routing --------------------------------

    def _run_agent_query(self, text: str) -> str:
        """Route a user query to the appropriate persona agent.

        Uses the AgentRegistry to match the utterance to an agent,
        then generates a response and speaks it with the agent's
        own Piper TTS voice.
        """
        agent, query = self.agent_registry.route_to_agent(text)

        if not agent:
            return "I couldn't determine which agent to route that to."

        logger.info(f"🎯 Agent routing: {agent.display_name} → '{query[:60]}'")

        # Run the agent's response generator
        try:
            import asyncio as _aio
            context = {"llm": self.llm} if self.llm else {}
            try:
                loop = _aio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    response = pool.submit(
                        _aio.run, agent.generate_response(query, context)
                    ).result(timeout=30)
            except RuntimeError:
                response = _aio.run(agent.generate_response(query, context))
        except Exception as e:
            logger.error(f"Agent {agent.display_name} response failed: {e}")
            return f"{agent.display_name} encountered an error: {e}"

        response = self._jarvisify(response)

        if self.memory:
            self.memory.add_turn("assistant", f"[{agent.display_name}] {response}")

        # Speak with the agent's own voice (if Piper is available)
        if self.audio and self.agent_registry:
            tts = getattr(self.audio, "tts", None)
            if tts:
                try:
                    self.agent_registry.speak_as(agent, response, tts)
                    # Return the text but DON'T let the pipeline speak it
                    # again — we already spoke it with the agent's voice.
                    return response
                except Exception as exc:
                    logger.warning(f"Agent voice swap failed: {exc}")

        return response
    
    def start(self) -> None:
        """
        Start D.A.E.M.O.N. main loop.
        
        Runs: Wake Word → Listen → Transcribe → Route (Skills/LLM) → Respond
        Features Phase 3 memory and Phase 4 custom skills.
        """
        if not self.audio:
            logger.error("Audio pipeline not available. Cannot start.")
            return
        
        self.is_running = True
        logger.info("🚀 D.A.E.M.O.N. Phase 6 starting main loop...")
        agent_names = ", ".join(self.agent_registry.agent_names) if self.agent_registry else "(none)"
        logger.info(f"   Available skills: Time, Calculator, Documents, System, Files, C Programs, Briefing")
        logger.info(f"   Active agents: {agent_names}")
        logger.info("   Press Ctrl+C to stop\n")

        # Start background polling (email, GitHub) if available
        if self.background_worker:
            try:
                import asyncio as _aio
                try:
                    _loop = _aio.get_running_loop()
                except RuntimeError:
                    _loop = None
                if _loop:
                    self.background_worker.start(loop=_loop)
                else:
                    # No event loop yet — start one in a thread
                    import threading
                    def _bg():
                        loop = _aio.new_event_loop()
                        _aio.set_event_loop(loop)
                        self.background_worker.start(loop=loop)
                        loop.run_forever()
                    _t = threading.Thread(target=_bg, daemon=True)
                    _t.start()
                logger.info("🔄 Background worker started (polling every 5 min)")
            except Exception as e:
                logger.warning(f"Background worker start failed: {e}")

        # Varied startup self-introduction — time-aware so it feels natural.
        try:
            import random as _random
            from datetime import datetime as _dt
            _hour = _dt.now().hour
            if _hour < 12:
                _time_prefix = "Good morning."
            elif _hour < 17:
                _time_prefix = "Good afternoon."
            else:
                _time_prefix = "Good evening."

            _intros = [
                "{tod}! I'm Daemon. I'm all set up and ready to go whenever you are.",
                "{tod}! Daemon here. Let me know if you need any help today.",
                "{tod}! I'm online and ready. What are we working on today?",
                "{tod}! Daemon's up and running. Just say my name if you need me.",
                "{tod}! I'm here. Let's get some work done, shall we?",
                "{tod}! I'm online. Feel free to just start talking or typing when you're ready.",
            ]
            greeting = _random.choice(_intros).format(tod=_time_prefix)
            self.audio.speak(greeting, allow_barge_in=False)
        except Exception as e:
            logger.debug(f"Startup greeting skipped: {e}")

        import time as _time
        consecutive_errors = 0
        MAX_BACKOFF = 30.0

        try:
            while self.is_running:
                try:
                    # Pipeline cycle with Phase 3 memory + Phase 4 skills
                    self.audio.full_pipeline(self.process_command)
                    consecutive_errors = 0  # reset on success

                    # Pause before next cycle
                    logger.info("\n" + "="*70 + "\n")

                except KeyboardInterrupt:
                    logger.info("\n⏹️  Stopping D.A.E.M.O.N...")
                    self.stop()
                    break
                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(2.0 ** consecutive_errors, MAX_BACKOFF)
                    logger.error(
                        f"Pipeline error #{consecutive_errors}: {str(e)} "
                        f"(retry in {backoff:.1f}s)"
                    )
                    if self.audio and consecutive_errors == 1:
                        # Only voice the error on the first failure to avoid spam.
                        try:
                            self.audio.speak("I encountered an error. Let me try again.")
                        except Exception:
                            pass
                    if consecutive_errors >= 6:
                        logger.error(
                            "Too many consecutive failures — stopping daemon. "
                            "Check microphone, network, and API keys."
                        )
                        self.stop()
                        break
                    _time.sleep(backoff)

        except KeyboardInterrupt:
            self.stop()
    
    def start_text_mode(self) -> None:
        """Run D.A.E.M.O.N. as a plain text chat (no microphone, no TTS).

        Useful when you want to debug skills/LLM behaviour, the room is too
        loud, or you simply prefer typing. Type ``/quit`` (or hit Ctrl+C) to
        exit, ``/clear`` to wipe the conversation memory.
        """
        self.is_running = True
        print()
        print("=" * 70)
        print("💬  D.A.E.M.O.N. — Text Chat Mode")
        print("=" * 70)
        print("Type your message and press Enter. Commands:")
        print("   /quit     - exit")
        print("   /clear    - clear conversation memory")
        print("   /status   - show system status")
        print("   /build    - start multi-agent build task")
        print("   /cancel   - cancel running orchestrator task")
        print("   /briefing - J.A.R.V.I.S. status briefing")
        print("   /poll     - force background data refresh")
        print("   /away     - enter AWAY mode (agents run background tasks)")
        print("   /active   - return to ACTIVE mode")
        print("   /agents   - list registered persona agents")
        print("=" * 70)
        print()

        try:
            while self.is_running:
                try:
                    text = input("you  ▸ ").strip()
                except EOFError:
                    break
                if not text:
                    continue
                if text.lower() in ("/quit", "/exit", ":q"):
                    break
                if text.lower() == "/clear":
                    if self.memory:
                        self.memory.clear()
                    print("🧹 Memory cleared.\n")
                    continue
                if text.lower() == "/status":
                    import json as _json
                    print(_json.dumps(self.get_status(), indent=2, default=str))
                    print()
                    continue
                if text.lower() == "/cancel":
                    if self.orchestrator:
                        self.orchestrator.cancel()
                        print("🛑 Cancel signal sent.\n")
                    else:
                        print("No orchestrator available.\n")
                    continue
                if text.lower().startswith("/build "):
                    task = text[7:].strip()
                    if not task:
                        print("Usage: /build <task description>\n")
                        continue
                    if not self.orchestrator:
                        print("Orchestrator not available.\n")
                        continue
                    print("🧠 Starting multi-agent build...")
                    try:
                        response = self._run_orchestrator_task(task)
                    except Exception as e:
                        response = f"Orchestrator error: {e}"
                    print(f"daemon ▸ {response}\n")
                    continue
                if text.lower() in ("/briefing", "/brief", "/status-update"):
                    if not self.briefing_engine:
                        print("Briefing engine not available.\n")
                        continue
                    print("🎬 Generating briefing...")
                    try:
                        response = self._run_briefing(text)
                    except Exception as e:
                        response = f"Briefing error: {e}"
                    print(f"daemon ▸ {response}\n")
                    continue
                if text.lower() == "/poll":
                    if not self.background_worker:
                        print("Background worker not available.\n")
                        continue
                    print("Polling email + GitHub...")
                    try:
                        result = self.background_worker.poll_now_sync()
                        email_r = result.get('email', {})
                        gh_r = result.get('github', {})
                        print(f"   Email: {email_r.get('unread_count', 0)} unread")
                        print(f"   GitHub: {gh_r.get('open_pr_count', 0)} open PRs")
                        if email_r.get('error'):
                            print(f"   Email warning: {email_r['error']}")
                        if gh_r.get('error'):
                            print(f"   GitHub warning: {gh_r['error']}")
                    except Exception as e:
                        print(f"   Poll error: {e}")
                    print()
                    continue

                # Phase 6: State management commands
                if text.lower() == "/away":
                    if not self.state_manager:
                        print("State manager not available.\n")
                        continue
                    if self.state_manager.go_away():
                        print("Entering AWAY mode. Background agents dispatched.\n")
                    else:
                        print(f"Cannot transition to AWAY from {self.state_manager.state.value}.\n")
                    continue
                if text.lower() == "/active":
                    if not self.state_manager:
                        print("State manager not available.\n")
                        continue
                    if self.state_manager.go_active():
                        print("Back to ACTIVE mode.\n")
                    else:
                        print(f"Cannot transition to ACTIVE from {self.state_manager.state.value}.\n")
                    continue
                if text.lower() == "/agents":
                    if not self.agent_registry:
                        print("Agent registry not available.\n")
                        continue
                    agents = self.agent_registry.list_agents()
                    if not agents:
                        print("No agents registered. Add agent files to agents/.\n")
                    else:
                        print(f"Registered agents ({len(agents)}):")
                        for a in agents:
                            print(f"   {a['display_name']:10s} - {a['description']}")
                            print(f"               voice: {a['voice_model']}")
                        print()
                    continue

                try:
                    response = self.process_command(text)
                except Exception as e:
                    logger.error(f"Command processing failed: {e}")
                    response = "Sorry, something went wrong handling that."
                print(f"daemon ▸ {response}\n")
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop D.A.E.M.O.N."""
        self.is_running = False
        # Gracefully stop background subsystems
        if self.background_worker:
            try:
                self.background_worker.stop()
            except Exception:
                pass
        if self.state_store:
            try:
                self.state_store.close()
            except Exception:
                pass
        # Phase 6: Shutdown agents
        if self.agent_registry:
            try:
                import asyncio as _aio
                try:
                    _aio.get_running_loop()
                    _aio.ensure_future(self.agent_registry.shutdown_all())
                except RuntimeError:
                    _aio.run(self.agent_registry.shutdown_all())
            except Exception:
                pass
        logger.info("✅ D.A.E.M.O.N. stopped")
    
    def get_status(self) -> dict:
        """Get D.A.E.M.O.N. status."""
        memory_stats = self.memory.get_stats() if self.memory else None
        skills = self.skill_router.get_available_skills() if self.skill_router else None
        
        return {
            "status": "running" if self.is_running else "stopped",
            "version": "Phase 6",
            "audio": self.audio.get_status() if self.audio else "unavailable",
            "llm": self.llm.get_info() if self.llm else "unavailable",
            "memory": memory_stats,
            "skills": skills,
            "orchestrator": self.orchestrator.get_status() if self.orchestrator else "unavailable",
            "background_worker": self.background_worker.get_status() if self.background_worker else "unavailable",
            "briefing": "ready" if self.briefing_engine else "unavailable",
            "state_manager": self.state_manager.get_status() if self.state_manager else "unavailable",
            "agent_registry": self.agent_registry.get_status() if self.agent_registry else "unavailable",
            "config": {
                "sample_rate": Config.SAMPLE_RATE,
                "wake_word": Config.WAKE_WORD,
                "llm_backend": Config.LLM_BACKEND
            }
        }


def main():
    """Main entry point."""
    # Parse arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "status":
            daemon = DAEMON()
            import json
            print(json.dumps(daemon.get_status(), indent=2))
            return
        elif sys.argv[1] == "devices":
            daemon = DAEMON()
            if daemon.audio:
                daemon.audio.list_devices()
            return
        elif sys.argv[1] == "test":
            logger.info("Testing audio components...")
            daemon = DAEMON()
            if daemon.audio:
                daemon.audio.speak("D.A.E.M.O.N. audio test successful!")
            return
    
    # Normal start
    daemon = DAEMON()
    try:
        daemon.start()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
