"""
Wake Word Detection.

Supports two backends, picked via Config.WAKE_WORD_ENGINE:

    openwakeword  - FREE, fully offline, no signup. Default.
                    Pre-trained models include: alexa, hey_mycroft,
                    hey_rhasspy, hey_jarvis. Selected via Config.WAKE_WORD_MODEL.

    porcupine     - Picovoice Porcupine. Needs free AccessKey from
                    https://console.picovoice.co/. Higher accuracy, lower CPU.

Public API used by audio_pipeline.py is preserved:
    .enabled
    .get_frame_length() -> int (samples)
    .get_sample_rate()  -> int
    .process(pcm: list[int]) -> bool
"""

import logging
from typing import Optional

from core_logic.config import Config
from core_logic.error_handler import WakeWordException

logger = logging.getLogger(__name__)

try:
    import pvporcupine
except ImportError:
    pvporcupine = None

try:
    import numpy as np
except ImportError:
    np = None


class WakeWordDetector:
    """Backend-agnostic wake word detector."""

    def __init__(self) -> None:
        engine = (Config.WAKE_WORD_ENGINE or "openwakeword").lower()
        self.enabled = False
        self._impl = None
        self._sample_rate = 16000
        self._frame_length = 1280  # 80 ms @ 16 kHz (openwakeword default)
        self.engine = engine

        if engine == "porcupine":
            self._init_porcupine()
        else:
            try:
                self._init_openwakeword()
            except Exception as e:
                logger.warning(f"openWakeWord init failed: {e}")
                if Config.PORCUPINE_ACCESS_KEY and pvporcupine is not None:
                    logger.info("Falling back to Porcupine.")
                    self._init_porcupine()
                else:
                    raise

    # ------------------------------------------------------------------ openWakeWord
    def _init_openwakeword(self) -> None:
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as e:
            raise WakeWordException(
                "openwakeword not installed. Run: pip install openwakeword"
            ) from e

        model_name = Config.WAKE_WORD_MODEL or "hey_mycroft"
        # Model files live under the openwakeword package; download once on first run.
        try:
            download_models(model_names=[model_name])
        except Exception as e:
            logger.warning(f"openWakeWord model download warning: {e}")

        try:
            self._impl = Model(
                wakeword_models=[model_name],
                inference_framework="onnx",
            )
        except Exception as e:
            raise WakeWordException(
                f"Failed to load openWakeWord model '{model_name}': {e}"
            ) from e

        self._model_name = model_name
        self._threshold = float(Config.WAKE_WORD_THRESHOLD)
        self._frame_length = 1280  # openWakeWord expects 80 ms chunks @ 16 kHz
        self._sample_rate = 16000
        self.enabled = True
        logger.info(
            f"✅ Wake word detector ready (openwakeword, model='{model_name}', "
            f"threshold={self._threshold})"
        )

    # ------------------------------------------------------------------ Porcupine
    def _init_porcupine(self) -> None:
        if pvporcupine is None:
            raise WakeWordException(
                "pvporcupine not installed. Run: pip install pvporcupine"
            )
        if not Config.PORCUPINE_ACCESS_KEY:
            raise WakeWordException(
                "PORCUPINE_ACCESS_KEY not set in .env. "
                "Get free key at https://console.picovoice.co/"
            )
        try:
            self._impl = pvporcupine.create(
                access_key=Config.PORCUPINE_ACCESS_KEY,
                keywords=[Config.WAKE_WORD],
            )
        except Exception as e:
            if "AccessKey" in str(e):
                raise WakeWordException(
                    f"Invalid Porcupine AccessKey: {e}"
                ) from e
            raise WakeWordException(f"Failed to initialize Porcupine: {e}") from e

        self._frame_length = self._impl.frame_length
        self._sample_rate = self._impl.sample_rate
        self.enabled = True
        logger.info(
            f"✅ Wake word detector ready (porcupine, keyword='{Config.WAKE_WORD}')"
        )

    # ------------------------------------------------------------------ public API
    def get_frame_length(self) -> int:
        return self._frame_length

    def get_sample_rate(self) -> int:
        return self._sample_rate

    def process(self, pcm) -> bool:
        if not self.enabled or self._impl is None:
            return False
        try:
            if self.engine == "porcupine":
                return self._impl.process(pcm) >= 0
            # openwakeword
            if np is None:
                logger.error("numpy required for openwakeword")
                return False
            arr = np.asarray(pcm, dtype=np.int16)
            scores = self._impl.predict(arr)
            score = float(scores.get(self._model_name, 0.0))
            if score >= self._threshold:
                logger.info(
                    f"🎯 WAKE WORD DETECTED: '{self._model_name}' (score={score:.2f})"
                )
                # Reset internal state to avoid immediate re-trigger on the same audio.
                try:
                    self._impl.reset()
                except Exception:
                    pass
                return True
            return False
        except Exception as e:
            logger.error(f"Wake word processing failed: {e}")
            return False

    def __del__(self) -> None:
        if getattr(self, "engine", None) == "porcupine" and getattr(self, "_impl", None):
            try:
                self._impl.delete()
            except Exception:
                pass
