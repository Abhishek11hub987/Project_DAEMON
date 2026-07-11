"""
Speech-to-Text Engine using OpenAI Whisper

Converts audio to text with automatic language detection.
"""

import numpy as np
import logging
from typing import Optional
from core_logic.error_handler import AudioException, retry_on_failure

logger = logging.getLogger(__name__)

try:
    import whisper
except ImportError:
    whisper = None


class SpeechToTextEngine:
    """Whisper-based speech recognition engine."""
    
    def __init__(self, model: str = "base"):
        """
        Initialize Whisper STT engine.
        
        Args:
            model: Model size ("tiny", "base", "small", "medium", "large")
                   Larger = more accurate but slower
        """
        if not whisper:
            raise AudioException("Whisper not installed. Run: pip install openai-whisper")
        
        try:
            self.model = whisper.load_model(model)
            self.model_name = model
            logger.info(f"✅ Whisper model '{model}' loaded")
        except Exception as e:
            raise AudioException(f"Failed to load Whisper model: {str(e)}")
    
    @retry_on_failure(max_attempts=3, delay=1.0)
    def transcribe(
        self,
        audio_data: np.ndarray,
        language: Optional[str] = None,
        temperature: float = 0.0,
        initial_prompt: Optional[str] = None,
    ) -> str:
        """
        Transcribe audio to text.

        Args:
            audio_data: Audio as numpy array (mono, 16kHz)
            language: Language code (e.g., "en", "es", "fr"). None = auto-detect
            temperature: Randomness (0.0 = deterministic)
            initial_prompt: Optional context string used to bias Whisper's
                vocabulary (helpful for proper nouns like "Daemon").

        Returns:
            Transcribed text

        Raises:
            AudioException: If transcription fails
        """
        try:
            logger.debug(f"Transcribing {len(audio_data)} samples...")

            # Normalize audio to [-1, 1] range for Whisper
            audio_normalized = audio_data.astype(np.float32) / 32768.0

            kwargs = dict(
                language=language,
                temperature=temperature,
                verbose=False,
                # condition_on_previous_text=False prevents the runaway
                # "I'm going to go to the next room. I'm going to..." style
                # hallucinations Whisper falls into on short / silent audio.
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
                beam_size=3,
            )
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt

            # Transcribe
            result = self.model.transcribe(audio_normalized, **kwargs)
            
            text = result["text"].strip()
            detected_lang = result.get("language", "unknown")

            # --- Hallucination guard ---
            # When Whisper gets faint/silent audio it sometimes parrots the
            # initial_prompt verbatim.  Detect and suppress that.
            if initial_prompt and text:
                from difflib import SequenceMatcher
                ratio = SequenceMatcher(None, text.lower(), initial_prompt.lower()).ratio()
                if ratio > 0.55:
                    logger.warning(
                        f"🚫 Suppressed hallucinated transcript (similarity={ratio:.2f}): '{text}'"
                    )
                    return ""

            # Catch common Whisper hallucination phrases that appear on
            # near-silent audio with deceptively high confidence.
            _HALLUCINATION_PHRASES = {
                "thank you", "thanks", "thanks for watching",
                "thank you for watching", "you", "bye",
                "subscribe", "like and subscribe",
            }
            if text.lower().rstrip(".!?, ") in _HALLUCINATION_PHRASES:
                # Only suppress if the audio RMS is low (i.e. probably not real speech).
                # We check via no_speech_prob from segments as a proxy.
                segments = result.get("segments", [])
                if segments:
                    no_speech = max(s.get("no_speech_prob", 0) for s in segments)
                    if no_speech > 0.3:
                        logger.warning(
                            f"🚫 Common hallucination suppressed "
                            f"(no_speech={no_speech:.2f}): '{text}'"
                        )
                        return ""

            # Drop segments where Whisper has very low confidence.
            segments = result.get("segments", [])
            if segments:
                avg_lp = sum(s.get("avg_logprob", 0) for s in segments) / len(segments)
                no_speech = max(s.get("no_speech_prob", 0) for s in segments)
                if avg_lp < -1.5 or no_speech > 0.8:
                    logger.warning(
                        f"🚫 Low-confidence transcript dropped "
                        f"(avg_logprob={avg_lp:.2f}, no_speech={no_speech:.2f}): '{text}'"
                    )
                    return ""
            
            logger.info(f"📝 Transcribed ({detected_lang}): '{text}'")
            return text
            
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise AudioException(f"STT failed: {str(e)}")
    
    def get_model_info(self) -> dict:
        """Get information about the loaded model."""
        return {
            "model": self.model_name,
            "type": "OpenAI Whisper",
            "task": "Speech-to-Text"
        }
