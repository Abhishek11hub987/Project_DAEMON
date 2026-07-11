"""
Master Audio Pipeline

Orchestrates the complete audio flow:
Wake Word → Listen → Transcribe → Process → Respond
"""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional, Callable

import numpy as np

from audio.microphone import MicrophoneRecorder
from audio.stt_engine import SpeechToTextEngine
from audio.tts_engine import TextToSpeechEngine
from audio.wake_word import WakeWordDetector
from audio.audio_config import get_default_input_device, print_audio_devices
from core_logic.config import Config
from core_logic.error_handler import AudioException

logger = logging.getLogger(__name__)


class AudioPipeline:
    """
    Unified audio interface: Wake → Listen → Transcribe → Respond
    
    Handles all audio I/O for D.A.E.M.O.N.
    """
    
    def __init__(self):
        """Initialize all audio components."""
        logger.info("🎵 Initializing audio pipeline...")
        
        # Microphone for input
        self.microphone = MicrophoneRecorder(
            sample_rate=Config.SAMPLE_RATE,
            chunk_size=Config.CHUNK_SIZE,
            channels=Config.CHANNELS
        )
        
        # Speech recognition
        try:
            self.stt = SpeechToTextEngine(model=getattr(Config, "WHISPER_MODEL", "small"))
            logger.info("✅ Speech-to-Text engine loaded")
        except Exception as e:
            logger.error(f"STT initialization failed: {str(e)}")
            self.stt = None
        
        # Speech synthesis
        try:
            self.tts = TextToSpeechEngine(rate=150, volume=0.9)
            logger.info("✅ Text-to-Speech engine loaded")
        except Exception as e:
            logger.error(f"TTS initialization failed: {str(e)}")
            self.tts = None
        
        # Wake word detection
        try:
            self.wake_word = WakeWordDetector()
            logger.info("✅ Wake word detector loaded")
        except Exception as e:
            logger.warning(f"Wake word detector not available: {str(e)}")
            self.wake_word = None
        
        # Audio state
        self.is_listening = False

        # Conversational mode: once the wake word triggers a session, follow-up
        # questions don't need the wake word again. We exit the session when
        # the user says an end phrase ("thanks / done / stop") OR after a
        # short silence timeout. Toggled by the pipeline itself.
        self.conversation_active = False
        # Callbacks the host application can subscribe to (web UI, tray, etc).
        # All are optional and called best-effort.
        self.status_callback: Optional[Callable[[str], None]] = None
        self.on_session_start: Optional[Callable[[], None]] = None
        self.on_session_end: Optional[Callable[[str], None]] = None  # arg = reason
        self.on_user_message: Optional[Callable[[str], None]] = None
        self.on_assistant_message: Optional[Callable[[str], None]] = None
        self._current_state = "idle"

        logger.info("✅ Audio pipeline initialized")

    # Phrases that gracefully end an active conversation. Matched as a whole
    # utterance OR as the trailing clause (e.g. "thanks daemon").
    _END_PHRASES = (
        "thanks", "thank you", "thanks daemon", "thank you daemon",
        "that's all", "thats all", "that is all",
        "that'll be all", "thatll be all",
        "we're done", "were done", "i'm done", "im done",
        "stop", "stop talking", "be quiet", "shut up",
        "goodbye", "good bye", "bye", "bye daemon", "bye bye",
        "exit", "end conversation", "end session", "end chat",
        "nothing else", "no thanks", "no thank you",
        "okay daemon thanks", "ok thanks",
    )

    @classmethod
    def _is_end_phrase(cls, text: str) -> bool:
        """Return True if `text` indicates the user wants to end the session."""
        if not text:
            return False
        norm = text.lower().strip().strip(" .,!?'\"")
        if not norm:
            return False
        if norm in cls._END_PHRASES:
            return True
        # Match as a clean trailing clause too, e.g. "okay daemon, thanks".
        for phrase in cls._END_PHRASES:
            if norm.endswith(" " + phrase):
                return True
        return False

    def _set_state(self, state: str) -> None:
        """Update the externally-observable status and notify any observer."""
        if state == self._current_state:
            return
        self._current_state = state
        cb = self.status_callback
        if cb is None:
            return
        try:
            cb(state)
        except Exception as e:
            logger.debug(f"status_callback error: {e}")

    @staticmethod
    def _fire(cb, *args) -> None:
        """Best-effort call of an optional observer callback."""
        if cb is None:
            return
        try:
            cb(*args)
        except Exception as e:
            logger.debug(f"observer callback error: {e}")

    def _begin_conversation(self) -> None:
        """Mark the start of an active conversation session."""
        if self.conversation_active:
            return
        self.conversation_active = True
        logger.info("💬 Conversation session started.")
        self._fire(self.on_session_start)

    def _end_conversation(self, reason: str = "user_ended") -> None:
        """Mark the end of the active session and return to wake-word mode."""
        if not self.conversation_active:
            return
        self.conversation_active = False
        # Release the microphone so the OS indicator light turns off.
        try:
            if self.microphone and self.microphone.is_recording:
                self.microphone.stop_recording()
        except Exception as e:
            logger.debug(f"mic stop on conversation end: {e}")
        logger.info(f"🛑 Conversation session ended ({reason}). Back to wake word.")
        self._set_state("idle")
        self._fire(self.on_session_end, reason)
    
    def list_devices(self) -> None:
        """Print available audio devices for debugging."""
        print_audio_devices()
    
    # Wake-word keywords that count as triggers when used in vad_keyword mode.
    # We accept "daemon" plus common Whisper mishearings observed in practice.
    _VAD_WAKE_WORDS = (
        "daemon", "daemons",
        "demon", "demons",
        "deamon", "deamons",
        "damon", "damen", "damion",
        "diamond", "dimon", "dimond",
        "dameon", "dayman", "dayman's",
        "dame", "dame on",
    )
    # Whisper sometimes only catches the "-mon" / "-man" tail of "Daemon".
    # These short tails alone aren't enough, but combined with phonetic match
    # below they help.
    _WAKE_PHONETIC_PREFIX = ("da", "de", "di", "day")

    @classmethod
    def _is_wake_token(cls, token: str) -> bool:
        """Return True if a single token sounds like 'daemon'."""
        t = token.lower().strip(" ,.!?'\"")
        if not t:
            return False
        if t in cls._VAD_WAKE_WORDS:
            return True
        # Fuzzy phonetic: starts with da/de/di/day AND ends with 'mon'/'men'/'man'.
        if any(t.startswith(p) for p in cls._WAKE_PHONETIC_PREFIX) and t.endswith(
            ("mon", "men", "man", "mond")
        ):
            return True
        # Fuzzy string similarity to "daemon" (catches "daimon", "daiman" etc.)
        from difflib import SequenceMatcher
        if SequenceMatcher(None, t, "daemon").ratio() >= 0.75:
            return True
        return False

    @classmethod
    def _strip_wake_word(cls, transcript: str) -> tuple[bool, str]:
        """Return (triggered, remainder). Triggered=True if a wake-word is found
        anywhere in the first ~5 tokens. The remainder is the rest of the
        utterance with the wake-word stripped, suitable as a command.
        """
        if not transcript:
            return False, ""
        cleaned = transcript.strip()
        if not cleaned:
            return False, ""

        # Tokenize using a simple word-character split that preserves order.
        tokens = re.findall(r"[A-Za-z']+|[^A-Za-z'\s]", cleaned)
        lowered = [t.lower() for t in tokens]

        # Find the wake word within the first 5 words (allows "hey daemon",
        # "ok daemon", "uh daemon listen up").
        wake_idx = -1
        for i, w in enumerate(lowered[:5]):
            if cls._is_wake_token(w):
                wake_idx = i
                break

        if wake_idx < 0:
            return False, ""

        # Build the remainder from tokens AFTER the wake-word.
        remainder_tokens = tokens[wake_idx + 1:]
        remainder = " ".join(remainder_tokens)
        # Tidy spacing around punctuation
        remainder = re.sub(r"\s+([,.?!])", r"\1", remainder).strip(" ,.")
        return True, remainder

    def listen_and_capture(self) -> tuple[bool, str]:
        """VAD + STT wake-word capture (engine == 'vad_keyword').

        Continuously listens for any voice activity, transcribes it, and
        checks whether the user said "Daemon". When they do, the *entire*
        utterance is returned so the rest can be processed as a command in
        the same breath (e.g. "Daemon what time is it").

        Returns:
            (triggered, remainder_text):
                triggered=True if wake word was heard;
                remainder_text is everything the user said after "Daemon"
                (may be empty if they just called the name).
        """
        if not self.stt:
            logger.warning("vad_keyword wake mode requires STT — not available.")
            return False, ""

        logger.info("👂 Waiting for wake word 'Daemon' (mic opens per burst — indicator off when idle)...")

        import time as _t

        silence_threshold = float(getattr(Config, "VAD_SILENCE_THRESHOLD", 500.0))
        silence_seconds   = float(getattr(Config, "VAD_SILENCE_SECONDS", 0.7))

        try:
            from audio.hotkeys import PTT_REQUESTED
        except Exception:
            PTT_REQUESTED = None  # type: ignore[assignment]

        def _open_mic() -> bool:
            """Open mic with backoff retry. Returns True on success."""
            for _attempt in range(4):
                try:
                    self.microphone.start_recording()
                    return True
                except Exception as e:
                    if _attempt < 3:
                        _w = 0.4 * (2 ** _attempt)
                        logger.warning(f"Mic open failed ({_attempt+1}): {e} — retry in {_w:.1f}s")
                        _t.sleep(_w)
                        try:
                            self._reset_microphone()
                        except Exception:
                            pass
                    else:
                        logger.error(f"Mic open failed after retries: {e}")
            return False

        def _close_mic() -> None:
            try:
                if self.microphone.is_recording:
                    self.microphone.stop_recording()
            except Exception:
                pass

        while self.is_listening:
            # PTT fast-path — bail and let the pipeline handle it
            if PTT_REQUESTED is not None and PTT_REQUESTED.is_set():
                logger.info("⌨️  PTT pressed — aborting wake listener.")
                return False, ""

            # Open mic for ONE short burst (max 5 s), then close it.
            # The OS mic indicator only lights up during these ~5 s windows.
            if not _open_mic():
                _t.sleep(1.0)
                continue

            try:
                audio = self.microphone.record_until_silence(
                    max_duration=5.0,
                    silence_threshold=silence_threshold,
                    silence_duration=silence_seconds,
                    initial_grace=5.0,
                )
            except Exception as e:
                logger.warning(f"Wake record failed: {e}")
                _close_mic()
                _t.sleep(0.5)
                continue
            finally:
                _close_mic()   # always close — mic indicator turns off here

            if audio is None or audio.size == 0:
                continue

            duration_s = audio.size / float(Config.SAMPLE_RATE)
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if audio.size else 0.0

            if audio.size < int(Config.SAMPLE_RATE * 0.2) or rms < 800:
                continue   # too short / too quiet — skip without logging spam

            logger.info(f"🎙️  Got audio burst: {duration_s:.2f}s, RMS={rms:.0f} — transcribing...")
            try:
                transcript = self.stt.transcribe(
                    audio,
                    language="en",
                    initial_prompt=(
                        "The user is talking to a voice assistant named "
                        "Daemon. They often start commands with the word 'Daemon'."
                    ),
                )
            except Exception as e:
                logger.warning(f"Transcribe failed in wake loop: {e}")
                continue

            transcript = (transcript or "").strip()
            if not transcript:
                continue

            logger.info(f"👂 Heard: {transcript!r}")
            triggered, remainder = self._strip_wake_word(transcript)
            if triggered:
                logger.info(f"🎯 Wake word detected. Remainder: {remainder!r}")
                return True, remainder
            logger.info("   (no wake word — say 'Daemon' to start)")

        return False, ""

    def listen_for_wake_word(self, on_detected: Optional[Callable] = None) -> None:
        """
        Listen continuously for wake word.
        
        Args:
            on_detected: Optional callback when wake word is detected
        """
        if not self.wake_word or not self.wake_word.enabled:
            logger.warning("Wake word detection not available. Manual trigger mode.")
            return
        
        logger.info(f"🎤 Listening for wake word: '{Config.WAKE_WORD}'...")
        logger.info("   Say the wake word to start recording...")
        
        self.is_listening = True
        
        import time as _t2
        for _att in range(4):
            try:
                self.microphone.start_recording()
                break
            except Exception as e:
                if _att < 3:
                    _w = 0.5 * (2 ** _att)
                    logger.warning(f"Mic open failed (attempt {_att+1}): {e} — retrying in {_w:.1f}s")
                    _t2.sleep(_w)
                    try:
                        self._reset_microphone()
                    except Exception:
                        pass
                else:
                    logger.error(f"Mic open failed after retries: {e}")
                    raise

        try:
            frame_length = self.wake_word.get_frame_length()
            frame_bytes = frame_length * 2  # int16 samples -> 2 bytes each
            buffer = bytearray()

            while self.is_listening:
                chunk = self.microphone.read_chunk()
                if chunk:
                    buffer.extend(chunk)

                # Drain full frames from the rolling buffer.
                while len(buffer) >= frame_bytes and self.is_listening:
                    frame = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]

                    pcm = np.frombuffer(frame, dtype=np.int16).tolist()
                    if self.wake_word.process(pcm):
                        logger.info("🎯 Wake word detected!")
                        self.is_listening = False
                        if on_detected:
                            on_detected()
                        break

            self.microphone.stop_recording()

        except Exception as e:
            logger.error(f"Error during wake word listening: {str(e)}")
            self.microphone.stop_recording()
            raise
    
    def record_command(self, duration: float = 15.0) -> np.ndarray:
        """
        Record a user voice command using voice-activity detection.

        Stops automatically when the user pauses (~1.5 s of silence) rather
        than waiting a fixed duration. ``duration`` is now treated as the
        maximum safety cap, not a fixed length.
        """
        logger.info(f"🎙️  Listening (auto-stop on silence, max {duration:.0f}s)...")

        try:
            audio_data = self.microphone.record_until_silence(
                max_duration=duration,
                silence_threshold=float(getattr(Config, "VAD_SILENCE_THRESHOLD", 500.0)),
                silence_duration=float(getattr(Config, "VAD_SILENCE_SECONDS", 1.5)),
                initial_grace=float(getattr(Config, "VAD_INITIAL_GRACE", 4.0)),
            )
            logger.info(f"✅ Recording complete ({len(audio_data)} samples, "
                        f"{len(audio_data)/Config.SAMPLE_RATE:.1f}s)")
            return audio_data
        except Exception as e:
            logger.error(f"Recording failed: {str(e)}")
            try:
                self.microphone.stop_recording()
            except Exception:
                pass
            raise AudioException(f"Recording failed: {str(e)}")
    
    def transcribe_audio(self, audio: np.ndarray, language: Optional[str] = None) -> str:
        """
        Convert audio to text.
        
        Args:
            audio: Audio data as numpy array
            language: Language code (None = auto-detect)
            
        Returns:
            Transcribed text
        """
        if not self.stt:
            raise AudioException("Speech-to-Text not available")
        
        try:
            text = self.stt.transcribe(audio, language=language)
            return text
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise AudioException(f"Transcription failed: {str(e)}")
    
    def speak(self, text: str, allow_barge_in: bool = True) -> bool:
        """
        Speak response text with optional barge-in support.

        When barge-in is enabled, a background thread monitors the microphone
        for sustained voice energy. If the user starts talking the assistant
        stops mid-sentence immediately.

        Args:
            text: Text to speak.
            allow_barge_in: If True, listen for user interruption while speaking.

        Returns:
            True if speech was interrupted by the user (barge-in), False otherwise.
        """
        if not self.tts:
            logger.warning("Text-to-Speech not available. Would say: " + text)
            return False

        if not allow_barge_in:
            try:
                self.tts.speak(text)
            except Exception as e:
                logger.error(f"Speech failed: {str(e)}")
            return False

        # Split into sentences for finer interrupt granularity / more human cadence.
        sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
        sentences = [s for s in sentences if s.strip()]
        if not sentences:
            return False

        interrupted = {"flag": False}
        listener_stop = threading.Event()

        def _barge_in_listener() -> None:
            """Background mic monitor that triggers when user starts speaking."""
            bg_mic = None
            try:
                from audio.microphone import MicrophoneRecorder
                bg_mic = MicrophoneRecorder(
                    sample_rate=Config.SAMPLE_RATE,
                    chunk_size=Config.CHUNK_SIZE,
                    channels=Config.CHANNELS,
                )
                bg_mic.start_recording()
            except Exception as e:
                logger.debug(f"Barge-in listener could not open mic: {e}")
                return

            # Grace period to ignore initial pops/clicks of TTS startup.
            grace_until = time.time() + 0.4
            sustained_ms = 0
            threshold = float(getattr(Config, "BARGE_IN_THRESHOLD", 1500))
            required_ms = int(getattr(Config, "BARGE_IN_SUSTAIN_MS", 250))

            try:
                while not listener_stop.is_set():
                    chunk = bg_mic.read_chunk()
                    if not chunk:
                        continue
                    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                    if samples.size == 0:
                        continue
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    chunk_ms = int(1000 * samples.size / Config.SAMPLE_RATE)
                    if time.time() < grace_until:
                        continue
                    if rms > threshold:
                        sustained_ms += chunk_ms
                        if sustained_ms >= required_ms:
                            logger.info(
                                f"🛑 Barge-in detected (RMS={rms:.0f} > {threshold:.0f}); stopping speech."
                            )
                            interrupted["flag"] = True
                            try:
                                self.tts.stop()
                            except Exception:
                                pass
                            return
                    else:
                        sustained_ms = max(0, sustained_ms - chunk_ms)
            finally:
                try:
                    if bg_mic is not None:
                        bg_mic.stop_recording()
                except Exception:
                    pass

        listener_thread = threading.Thread(target=_barge_in_listener, daemon=True)
        listener_thread.start()

        try:
            for sentence in sentences:
                if interrupted["flag"]:
                    break
                try:
                    self.tts.speak(sentence)
                except Exception as e:
                    logger.error(f"Speech failed: {str(e)}")
                    break
                if self.tts.was_interrupted():
                    interrupted["flag"] = True
                    break
                # Tiny natural pause between sentences (also a barge-in window).
                time.sleep(0.08)
        finally:
            listener_stop.set()
            listener_thread.join(timeout=0.5)

        return interrupted["flag"]
    
    def _reset_microphone(self) -> None:
        """Recreate the microphone recorder after a disconnection or stream error."""
        try:
            if self.microphone:
                try:
                    self.microphone.stop_recording()
                except Exception:
                    pass
            self.microphone = MicrophoneRecorder(
                sample_rate=Config.SAMPLE_RATE,
                chunk_size=Config.CHUNK_SIZE,
                channels=Config.CHANNELS,
            )
            logger.info("🎙️  Microphone re-initialized")
        except Exception as e:
            logger.error(f"Microphone reset failed: {str(e)}")

    def full_pipeline(self, process_func: Callable[[str], str]) -> None:
        """
        Run complete pipeline: Wake → Listen → Transcribe → Process → Speak

        Resilient to:
            - Empty / silent transcriptions (skipped, no LLM call)
            - Microphone disconnects (recorder is re-created)
            - LLM/network errors (caught and surfaced via TTS)

        Args:
            process_func: Function that takes transcribed text and returns response
        """
        try:
            logger.info("🔄 Starting audio pipeline...")

            engine = (getattr(Config, "WAKE_WORD_ENGINE", "") or "").lower()
            user_input = ""

            # 0. Push-to-talk fast-path — if the user pressed the PTT hotkey
            #    while we were idle, skip wake-word entirely and record now.
            try:
                from audio.hotkeys import consume_ptt
                ptt = consume_ptt()
            except Exception:
                ptt = False

            # If we're already inside an active conversation, keep listening
            # for follow-ups (no wake word needed).
            if self.conversation_active and not ptt:
                self._set_state("listening")
                try:
                    audio = self.record_command(duration=12.0)
                except Exception as e:
                    logger.warning(f"Follow-up record failed: {e}")
                    self._end_conversation("error")
                    return
                user_input = self.transcribe_audio(audio).strip()
                if not user_input or len(user_input) < 2:
                    # Silence in active mode → end the session gracefully.
                    logger.info("👂 Silence in active conversation — ending session.")
                    self._end_conversation("silence_timeout")
                    return
            elif ptt:
                logger.info("⌨️  Push-to-talk: skipping wake word, recording now.")
                self._set_state("speaking")
                self.speak("Yes?", allow_barge_in=False)
                self._set_state("listening")
                try:
                    audio = self.record_command(duration=12.0)
                except Exception as e:
                    logger.error(f"PTT recording failed: {e}")
                    self._set_state("idle")
                    return
                self._set_state("thinking")
                user_input = self.transcribe_audio(audio).strip()
                self._begin_conversation()
            # 1. Wake word
            elif engine == "vad_keyword":
                # Single-utterance VAD+STT mode: user just says "Daemon ..."
                self.is_listening = True
                self._set_state("listening")
                try:
                    triggered, remainder = self.listen_and_capture()
                except Exception as e:
                    logger.warning(f"VAD wake listener errored: {e}. Resetting mic.")
                    self._reset_microphone()
                    return
                finally:
                    self.is_listening = False

                if not triggered:
                    return

                # Wake word detected → start a new conversation session.
                self._begin_conversation()

                # If they spoke the command in the same breath, use it directly.
                user_input = (remainder or "").strip()

                # Otherwise prompt and record a follow-up; if they stay
                # silent or say something very short, deliver a proactive
                # J.A.R.V.I.S.-style briefing instead of just "Yes?".
                if not user_input:
                    self.speak("I'm here.", allow_barge_in=False)
                    try:
                        audio = self.record_command(duration=8.0)
                    except (AudioException, RuntimeError) as e:
                        logger.warning(f"Recording failed ({e}); attempting mic reset.")
                        self._reset_microphone()
                        try:
                            audio = self.record_command(duration=8.0)
                        except Exception as e2:
                            logger.error(f"Recording failed after reset: {e2}")
                            self.speak("My microphone seems unavailable.")
                            self._end_conversation("error")
                            return
                    user_input = self.transcribe_audio(audio).strip()

                    # If they didn't say anything meaningful after the
                    # wake word, auto-trigger a status briefing.
                    if not user_input or len(user_input) < 3:
                        logger.info("👂 No follow-up command — triggering proactive briefing.")
                        user_input = "status update"
            elif self.wake_word and self.wake_word.enabled:
                try:
                    self.listen_for_wake_word()
                except AudioException as e:
                    logger.warning(f"Wake word listener errored: {e}. Resetting mic.")
                    self._reset_microphone()
                    return

                # Wake word detected → start a new conversation session.
                self._begin_conversation()
                self.speak("I'm here.", allow_barge_in=False)

                try:
                    audio = self.record_command(duration=8.0)
                except (AudioException, RuntimeError) as e:
                    logger.warning(f"Recording failed ({e}); attempting mic reset.")
                    self._reset_microphone()
                    try:
                        audio = self.record_command(duration=8.0)
                    except Exception as e2:
                        logger.error(f"Recording failed after reset: {e2}")
                        self.speak("My microphone seems unavailable.")
                        return
                user_input = self.transcribe_audio(audio).strip()

                # If they didn't say anything meaningful, auto-briefing.
                if not user_input or len(user_input) < 3:
                    logger.info("👂 No follow-up command — triggering proactive briefing.")
                    user_input = "status update"
            else:
                logger.info("⏳ Waiting for manual trigger...")
                try:
                    input("Press Enter to start recording...")
                except EOFError:
                    return
                try:
                    audio = self.record_command(duration=12.0)
                except Exception as e:
                    logger.error(f"Recording failed: {e}")
                    return
                user_input = self.transcribe_audio(audio).strip()

            if not user_input or len(user_input) < 2:
                logger.info("👂 No speech detected, returning to wake word.")
                if self.conversation_active:
                    self._end_conversation("silence_timeout")
                return
            logger.info(f"👤 User: {user_input}")
            self._fire(self.on_user_message, user_input)

            # If the user said an end-phrase, wrap up the session.
            if self._is_end_phrase(user_input):
                self._set_state("speaking")
                farewell = "Anytime. I'll be here when you need me."
                self.speak(farewell, allow_barge_in=False)
                self._fire(self.on_assistant_message, farewell)
                self._end_conversation("user_ended")
                self._set_state("idle")
                return

            # 4. Process command (LLM/skills can raise; handle gracefully)
            self._set_state("thinking")
            try:
                response = process_func(user_input)
            except Exception as e:
                logger.error(f"Command processing error: {e}")
                response = "I had trouble processing that. Please try again."

            logger.info(f"🤖 Response: {response}")
            self._fire(self.on_assistant_message, response)

            # 5. Speak response (with barge-in). When the user barges in, we
            #    DON'T break out — we stay in conversation_active mode and the
            #    next pipeline cycle will record the follow-up immediately.
            self._set_state("speaking")
            self.speak(response)
            self._set_state("idle")
            logger.info("✅ Pipeline turn completed (conversation active)" if self.conversation_active else "✅ Pipeline completed")

        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            try:
                self.speak("Sorry, something went wrong.")
            except Exception:
                pass
    
    def get_status(self) -> dict:
        """Get pipeline status."""
        return {
            "microphone": "ready" if self.microphone else "unavailable",
            "stt": "ready" if self.stt else "unavailable",
            "tts": "ready" if self.tts else "unavailable",
            "wake_word": "ready" if (self.wake_word and self.wake_word.enabled) else "unavailable",
            "listening": self.is_listening
        }
