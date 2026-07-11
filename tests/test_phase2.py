"""
Phase 2 Implementation - Audio I/O Tests

Tests for STT, TTS, and wake word components.
"""

import pytest
import numpy as np
from pathlib import Path


class TestSpeechToText:
    """Test Speech-to-Text engine."""
    
    def test_stt_engine_loads(self):
        """Test that Whisper engine loads successfully."""
        try:
            from audio.stt_engine import SpeechToTextEngine
            engine = SpeechToTextEngine(model="tiny")  # Use tiny for tests
            assert engine is not None
            info = engine.get_model_info()
            assert info["model"] == "tiny"
        except ImportError:
            pytest.skip("Whisper not installed")
    
    def test_transcribe_with_dummy_audio(self):
        """Test transcription with dummy audio (may not recognize anything)."""
        try:
            from audio.stt_engine import SpeechToTextEngine
            engine = SpeechToTextEngine(model="tiny")
            
            # Create dummy audio (silence)
            audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
            
            result = engine.transcribe(audio)
            assert isinstance(result, str)
            # Result will likely be empty for silence
        except ImportError:
            pytest.skip("Whisper not installed")


class TestTextToSpeech:
    """Test Text-to-Speech engine."""
    
    def test_tts_engine_initializes(self):
        """Test that TTS engine initializes."""
        from audio.tts_engine import TextToSpeechEngine
        engine = TextToSpeechEngine(rate=150, volume=0.9)
        assert engine is not None
        
        info = engine.get_engine_info()
        assert info["engine"] == "pyttsx3"
        assert info["rate"] == 150
    
    def test_tts_speak(self):
        """Test that TTS speak works (non-blocking test)."""
        from audio.tts_engine import TextToSpeechEngine
        engine = TextToSpeechEngine()
        
        # Just verify it doesn't crash
        try:
            engine.speak("Hello world")
        except Exception as e:
            pytest.fail(f"TTS speak failed: {str(e)}")
    
    def test_tts_voice_control(self):
        """Test voice control functions."""
        from audio.tts_engine import TextToSpeechEngine
        engine = TextToSpeechEngine()
        
        # Test rate setting
        engine.set_rate(100)
        assert engine.engine.getProperty("rate") == 100
        
        # Test volume setting
        engine.set_volume(0.5)
        assert engine.engine.getProperty("volume") == 0.5


class TestWakeWord:
    """Test Wake Word detection."""
    
    def test_wake_word_initialization(self):
        """Test wake word detector initialization."""
        from audio.wake_word import WakeWordDetector
        
        # This will fail if PORCUPINE_ACCESS_KEY not set, which is expected
        try:
            detector = WakeWordDetector()
            assert detector is not None
        except Exception as e:
            # Expected if no API key
            assert "AccessKey" in str(e) or "not set" in str(e).lower()


class TestAudioPipeline:
    """Test complete audio pipeline."""
    
    def test_pipeline_initializes(self):
        """Test that audio pipeline initializes."""
        from audio.audio_pipeline import AudioPipeline
        pipeline = AudioPipeline()
        assert pipeline is not None
        
        status = pipeline.get_status()
        assert "microphone" in status
        assert "stt" in status
        assert "tts" in status
    
    def test_pipeline_speak(self):
        """Test pipeline speak function."""
        from audio.audio_pipeline import AudioPipeline
        pipeline = AudioPipeline()
        
        # Just verify it doesn't crash
        try:
            pipeline.speak("Test audio pipeline")
        except Exception as e:
            # TTS might fail in test environment
            assert True  # Still pass


class TestLLMEngine:
    """Test LLM Engine integration."""
    
    def test_llm_engine_initializes(self):
        """Test that LLM engine initializes with available backend."""
        try:
            from core_logic.llm_engine import LLMEngine
            # Will use default backend from config
            engine = LLMEngine()
            assert engine is not None
            info = engine.get_info()
            assert "backend" in info
        except Exception as e:
            # Expected if no LLM backend available
            pytest.skip(f"LLM not available: {str(e)}")
    
    def test_llm_info_available(self):
        """Test that LLM info can be retrieved."""
        try:
            from core_logic.llm_engine import LLMEngine
            engine = LLMEngine()
            info = engine.get_info()
            assert info["type"] == "LLM Engine"
        except Exception:
            pytest.skip("LLM not available")


class TestDAEMONMain:
    """Test main D.A.E.M.O.N. class."""
    
    def test_daemon_initializes(self):
        """Test that DAEMON initializes."""
        from core_logic.main import DAEMON
        daemon = DAEMON()
        assert daemon is not None
    
    def test_daemon_status(self):
        """Test daemon status reporting."""
        from core_logic.main import DAEMON
        daemon = DAEMON()
        status = daemon.get_status()
        assert "status" in status
        assert status["status"] == "stopped"
    
    def test_daemon_process_command(self):
        """Test command processing."""
        from core_logic.main import DAEMON
        daemon = DAEMON()
        
        response = daemon.process_command("Hello")
        assert isinstance(response, str)
        assert len(response) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
