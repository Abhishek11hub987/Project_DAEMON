"""Microphone Input Handler"""
import numpy as np
import sounddevice as sd
from core_logic.config import Config
import logging

logger = logging.getLogger(__name__)

class MicrophoneRecorder:
    """Manages microphone input and audio buffering."""

    def __init__(self, sample_rate=Config.SAMPLE_RATE, chunk_size=Config.CHUNK_SIZE, channels=Config.CHANNELS):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.stream = None
        self.is_recording = False

    def start_recording(self) -> None:
        """Start microphone recording."""
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.chunk_size,
                dtype='int16',
                device=Config.AUDIO_INPUT_DEVICE,
            )
            self.stream.start()
            self.is_recording = True
            logger.info("Microphone recording started")
        except Exception as e:
            raise RuntimeError(f"Failed to start microphone: {str(e)}")

    def stop_recording(self) -> None:
        """Stop microphone recording."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.is_recording = False
        logger.info("Microphone recording stopped")

    def read_chunk(self):
        """Read a chunk of audio data."""
        if not self.is_recording or not self.stream:
            return None
        try:
            data, overflowed = self.stream.read(self.chunk_size)
            return data.tobytes()
        except Exception as e:
            logger.error(f"Error reading audio: {str(e)}")
            return None

    def record_until_silence(
        self,
        max_duration: float = 12.0,
        silence_threshold: float = 500.0,
        silence_duration: float = 0.8,
        initial_grace: float = 4.0,
    ) -> np.ndarray:
        """Record audio with voice-activity detection.

        Starts the stream if it isn't already running, then records until
        ``silence_duration`` seconds of quiet have followed at least one
        voice burst — or until ``max_duration`` total seconds elapse.
        Returns the captured audio as a mono int16 numpy array.

        Args:
            max_duration: Hard cap on recording length (safety stop).
            silence_threshold: RMS amplitude below which audio is "silent".
            silence_duration: How many seconds of silence end the recording
                              (only after the user has spoken at least once).
            initial_grace: How long to wait for the user to start speaking
                           before giving up if nothing is heard.
        """
        import time

        # Divide threshold by gain so the VAD comparison works on raw signal
        # (avoids per-chunk gain multiplication which distorts the waveform).
        gain = float(getattr(Config, "MIC_GAIN", 1.0) or 1.0)
        effective_threshold = silence_threshold / max(gain, 0.1)

        own_stream = False
        if not self.is_recording:
            self.start_recording()
            own_stream = True

        chunks: list[bytes] = []
        start = time.time()
        last_voice_ts = None
        try:
            while True:
                chunk = self.read_chunk()
                if not chunk:
                    continue
                chunks.append(chunk)

                arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0
                elapsed = time.time() - start

                if rms > effective_threshold:
                    last_voice_ts = time.time()

                # Hard stops
                if elapsed > max_duration:
                    break
                # No speech detected at all within the grace window → bail.
                if last_voice_ts is None and elapsed > initial_grace:
                    break
                # User has spoken AND been quiet long enough → done.
                if last_voice_ts is not None and (time.time() - last_voice_ts) > silence_duration:
                    break
        finally:
            if own_stream:
                self.stop_recording()

        if not chunks:
            return np.zeros(0, dtype=np.int16)
        raw = b"".join(chunks)
        audio = np.frombuffer(raw, dtype=np.int16).copy()

        # --- Dynamic peak normalization ---
        # Instead of a fixed gain (which distorts), scale the whole recording
        # so the loudest sample reaches ~80% of int16 range (≈26214).
        # This preserves waveform shape and gives Whisper a clean signal.
        peak = int(np.abs(audio).max())
        if peak > 0:
            target_peak = 26000  # ~80% of 32767
            norm_gain = min(target_peak / peak, 20.0)  # cap at 20x for safety
            if norm_gain > 1.05:  # only boost if meaningfully quiet
                boosted = audio.astype(np.float32) * norm_gain
                np.clip(boosted, -32768, 32767, out=boosted)
                audio = boosted.astype(np.int16)
                logger.debug(
                    f"Audio normalized: peak {peak} -> {int(np.abs(audio).max())} "
                    f"(gain={norm_gain:.1f}x)"
                )
        return audio

    def record_for_duration(self, duration: float) -> np.ndarray:
        """Record audio for a specified duration.

        Args:
            duration: Recording duration in seconds

        Returns:
            Audio data as numpy array (mono, int16)
        """
        try:
            frames = int(self.sample_rate * duration)
            recording = sd.rec(
                frames,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                blocking=True
            )
            if recording.ndim > 1 and recording.shape[1] > 1:
                recording = recording.mean(axis=1, dtype=np.int16)
            else:
                recording = recording.flatten()
            return recording
        except Exception as e:
            raise RuntimeError(f"Failed to record audio: {str(e)}")

    def __enter__(self):
        self.start_recording()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_recording()
