"""Configuration tests"""
import pytest
from core_logic.config import Config

def test_config_exists():
    """Test that config loads successfully."""
    assert Config.SAMPLE_RATE == 16000
    assert Config.OLLAMA_MODEL == "llama2"
