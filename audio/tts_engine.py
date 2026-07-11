"""
Text-to-Speech Engine for D.A.E.M.O.N.

Supports four backends, picked from Config.TTS_ENGINE:

    piper       - Piper neural TTS (FREE, fully OFFLINE, OSS).
                  Recommended for fully open-source / privacy setups.
                  Voice via Config.TTS_VOICE, e.g. 'en_GB-alan-medium',
                  'en_GB-northern_english_male-medium', 'en_US-ryan-high'.
                  Models auto-downloaded to models/piper/ on first use.

    edge        - Microsoft Edge neural TTS (FREE, online).
                  Voice via Config.TTS_VOICE (en-GB-RyanNeural, ...).

    elevenlabs  - ElevenLabs cloud TTS (PAID, best quality).
                  Needs ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID in .env.

    pyttsx3     - Offline SAPI5/eSpeak fallback (robotic but works without
                  network). Tries to pick a male voice automatically.

The constructor accepts the same ``rate`` / ``volume`` knobs as before so the
rest of the codebase (audio_pipeline.py) doesn't need to change.
"""

import asyncio
import logging
import os
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _play_audio_file(path: str) -> None:
    """Play an audio file synchronously, cross-platform."""
    try:
        # Try sounddevice + soundfile (clean, no GUI)
        import sounddevice as sd
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32")
        sd.play(data, sr)
        sd.wait()
        return
    except Exception as e:
        logger.debug(f"sounddevice playback unavailable: {e}")

    # Windows fallback: winsound (PCM/WAV only)
    if os.name == "nt":
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return
        except Exception as e:
            logger.debug(f"winsound playback failed: {e}")

    # Last resort: shell out to a system player
    for player in ("ffplay", "afplay", "mpg123", "play"):
        from shutil import which
        if which(player):
            import subprocess
            subprocess.run(
                [player, "-nodisp", "-autoexit", path] if player == "ffplay" else [player, path],
                capture_output=True,
            )
            return
    logger.error("No audio playback backend available.")


class TextToSpeechEngine:
    """Multi-backend TTS engine with Jarvis-style defaults."""

    def __init__(self, rate: int = 175, volume: float = 1.0, voice_id: int = 0):
        from core_logic.config import Config  # local import to avoid cycles

        self.config = Config
        self.rate = rate
        self.volume = volume
        self.backend = (Config.TTS_ENGINE or "edge").lower()
        # TTS_VOICE is repurposed across backends:
        #   edge   -> 'en-GB-RyanNeural'
        #   piper  -> 'en_GB-alan-medium'
        self.voice = getattr(Config, "TTS_VOICE", None) or (
            "en_US-amy-medium" if self.backend == "piper" else "en-US-AnaNeural"
        )
        # Backwards compat alias
        self.edge_voice = self.voice
        self._pyttsx3 = None  # lazy
        self._piper_voice = None  # lazy
        self._piper_model_dir = Config.PROJECT_ROOT / "models" / "piper"

        # Barge-in / interruption state
        self._stop_event = threading.Event()
        self._is_speaking = False
        self._was_interrupted = False

        # Validate backend availability with graceful fallback chain
        if self.backend == "elevenlabs" and not Config.ELEVENLABS_API_KEY:
            logger.warning("ELEVENLABS_API_KEY missing — falling back to piper")
            self.backend = "piper"

        if self.backend == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                logger.warning(
                    "edge-tts not installed — falling back to piper."
                )
                self.backend = "piper"

        if self.backend == "piper":
            try:
                from piper import PiperVoice  # noqa: F401
            except ImportError:
                logger.warning(
                    "piper-tts not installed — run 'pip install piper-tts'. "
                    "Falling back to pyttsx3."
                )
                self.backend = "pyttsx3"

        logger.info(f"TTS backend: {self.backend} (voice={self.voice})")

    # ----- public API --------------------------------------------------------

    def speak(self, text: str) -> None:
        """Speak ``text`` synchronously. Returns early if stop() is called."""
        if not text or not text.strip():
            return
        logger.info(f"🔊 Speaking [{self.backend}]: '{text[:80]}{'...' if len(text) > 80 else ''}'")
        self._stop_event.clear()
        self._was_interrupted = False
        self._is_speaking = True
        try:
            if self.backend == "piper":
                self._speak_piper(text)
            elif self.backend == "edge":
                self._speak_edge(text)
            elif self.backend == "elevenlabs":
                self._speak_elevenlabs(text)
            else:
                self._speak_pyttsx3(text)
        except Exception as e:
            logger.error(f"TTS failed on backend '{self.backend}': {e}")
            # One-shot graceful fallback to pyttsx3 so the user still hears something
            if self.backend != "pyttsx3":
                logger.warning("Falling back to pyttsx3 for this utterance.")
                try:
                    self._speak_pyttsx3(text)
                except Exception as e2:
                    logger.error(f"pyttsx3 fallback also failed: {e2}")
        finally:
            self._is_speaking = False
            if self._stop_event.is_set():
                self._was_interrupted = True

    def speak_async(self, text: str) -> None:
        """Non-blocking speak (fires-and-forgets a worker thread)."""
        threading.Thread(target=self.speak, args=(text,), daemon=True).start()

    # ----- interruption control ---------------------------------------------

    def stop(self) -> None:
        """Immediately stop any in-progress speech."""
        self._stop_event.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        # Stop pyttsx3 engine if it's the active backend
        if self._pyttsx3 is not None:
            try:
                self._pyttsx3.stop()
            except Exception:
                pass

    def is_speaking(self) -> bool:
        return self._is_speaking

    def was_interrupted(self) -> bool:
        return self._was_interrupted

    # ----- Edge-TTS ----------------------------------------------------------

    def _speak_edge(self, text: str) -> None:
        import edge_tts

        # Map our WPM-style rate (~150-200) to Edge's "+N%/-N%" string.
        # Default voice cadence ≈ 150 wpm at rate="+0%".
        pct = int(round((self.rate - 150) / 150 * 100))
        rate_str = f"{pct:+d}%"
        volume_str = f"{int(round((self.volume - 1.0) * 100)):+d}%"

        async def _synthesize(out_path: str) -> None:
            communicate = edge_tts.Communicate(
                text,
                voice=self.edge_voice,
                rate=rate_str,
                volume=volume_str,
            )
            await communicate.save(out_path)

        tmp = tempfile.NamedTemporaryFile(prefix="daemon_tts_", suffix=".mp3", delete=False)
        tmp.close()
        try:
            asyncio.run(_synthesize(tmp.name))
            _play_audio_file(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # ----- Piper (offline neural) -------------------------------------------

    # Map a Piper voice short name to its path under rhasspy/piper-voices on HF.
    # If a voice isn't in this table we'll guess from its parts (lang_REGION-name-quality).
    _PIPER_HF_BASE = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    )

    def _piper_hf_paths(self, voice: str) -> tuple[str, str]:
        """Return (onnx_url, json_url) on HuggingFace for a given voice short name."""
        # Voice format: <lang>_<REGION>-<name>-<quality>
        try:
            lang_region, name, quality = voice.split("-")
            lang = lang_region.split("_")[0]
            sub = f"{lang}/{lang_region}/{name}/{quality}/{voice}"
        except ValueError as e:
            raise RuntimeError(
                f"Unrecognised Piper voice '{voice}'. "
                "Use 'lang_REGION-name-quality', e.g. 'en_GB-alan-medium'."
            ) from e
        return (
            f"{self._PIPER_HF_BASE}/{sub}.onnx",
            f"{self._PIPER_HF_BASE}/{sub}.onnx.json",
        )

    def _ensure_piper_voice(self):
        """Download (if needed) and load the Piper voice. Cached after first call."""
        if self._piper_voice is not None:
            return self._piper_voice

        from piper import PiperVoice
        import requests

        self._piper_model_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = self._piper_model_dir / f"{self.voice}.onnx"
        json_path = self._piper_model_dir / f"{self.voice}.onnx.json"

        if not onnx_path.exists() or not json_path.exists():
            onnx_url, json_url = self._piper_hf_paths(self.voice)
            logger.info(f"Downloading Piper voice '{self.voice}'...")
            for url, dest in [(json_url, json_path), (onnx_url, onnx_path)]:
                with requests.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1 << 16):
                            f.write(chunk)
                    tmp.replace(dest)
            logger.info(f"Piper voice cached at {onnx_path}")

        self._piper_voice = PiperVoice.load(str(onnx_path))
        return self._piper_voice

    def _speak_piper(self, text: str) -> None:
        import io
        import wave
        import numpy as np
        import sounddevice as sd

        voice = self._ensure_piper_voice()

        # Synthesize to an in-memory WAV, then play with sounddevice.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        buf.seek(0)

        with wave.open(buf, "rb") as wav_file:
            sr = wav_file.getframerate()
            n = wav_file.getnframes()
            raw = wav_file.readframes(n)

        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        # Apply user volume (Piper output is already loud, this lightly trims)
        audio *= max(0.0, min(1.0, self.volume))

        # Non-blocking playback + poll for stop event (barge-in friendly).
        sd.play(audio, sr)
        try:
            while sd.get_stream().active:
                if self._stop_event.is_set():
                    sd.stop()
                    break
                time.sleep(0.03)
        except Exception:
            sd.wait()  # fallback if stream introspection unsupported

    # ----- ElevenLabs --------------------------------------------------------

    def _speak_elevenlabs(self, text: str) -> None:
        import requests
        api_key = self.config.ELEVENLABS_API_KEY
        voice_id = self.config.ELEVENLABS_VOICE_ID
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.85},
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(prefix="daemon_tts_", suffix=".mp3", delete=False)
        tmp.write(resp.content)
        tmp.close()
        try:
            _play_audio_file(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # ----- pyttsx3 (offline fallback) ----------------------------------------

    def _get_pyttsx3(self):
        """Create a fresh pyttsx3 engine per call — fixes the runAndWait deadlock."""
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        engine.setProperty("volume", self.volume)
        # Try to pick a soft female voice to match Amy persona
        try:
            voices = engine.getProperty("voices")
            chosen = None
            for v in voices:
                name = (v.name or "").lower()
                if "zira" in name or "hazel" in name or "female" in name or "eva" in name:
                    chosen = v
                    break
            if chosen is None and voices:
                chosen = voices[0]
            if chosen:
                engine.setProperty("voice", chosen.id)
        except Exception:
            pass
        return engine

    def _speak_pyttsx3(self, text: str) -> None:
        engine = self._get_pyttsx3()
        engine.say(text)
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:
            pass
        del engine  # release the COM object on Windows

    # ----- introspection -----------------------------------------------------

    def list_voices(self) -> list:
        """Return available voices for the active backend."""
        if self.backend == "edge":
            try:
                import edge_tts
                voices = asyncio.run(edge_tts.list_voices())
                return [
                    {"id": v["ShortName"], "name": v["FriendlyName"]}
                    for v in voices if v.get("Locale", "").startswith("en")
                ]
            except Exception as e:
                logger.error(f"Failed to list Edge voices: {e}")
                return []
        # pyttsx3
        try:
            engine = self._get_pyttsx3()
            voices = engine.getProperty("voices")
            return [{"id": v.id, "name": v.name} for v in voices]
        except Exception:
            return []

    def get_engine_info(self) -> dict:
        return {
            "engine": self.backend,
            "voice": self.edge_voice if self.backend == "edge" else "(backend-default)",
            "rate": self.rate,
            "volume": self.volume,
        }

    # Stubs retained for backwards compatibility with old call sites
    def set_rate(self, rate: int) -> None:
        self.rate = rate

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))

    def set_voice(self, voice_id) -> None:
        if isinstance(voice_id, str):
            self.edge_voice = voice_id
            logger.info(f"Edge voice -> {voice_id}")
