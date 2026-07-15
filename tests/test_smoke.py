import pytest
from core_logic.config import Config
from core_logic.llm_engine import LLMEngine
from core_logic.skill_router import SkillRouter
from skills.c_integration_skill import CIntegrationSkill

@pytest.mark.smoke
def test_config_loads_properly():
    """Verify that configuration loads and a backend is set."""
    assert Config.LLM_BACKEND in ["ollama", "groq", "gemini", "openai"]

@pytest.mark.smoke
def test_skill_router_basic():
    """Verify that the SkillRouter can parse a simple skill command."""
    router = SkillRouter()
    # Execute a simple time check
    response = router.execute_command("what time is it")
    # Should return a string mentioning the time or date, not an error
    assert isinstance(response, str)
    assert len(response) > 0
    assert "error" not in response.lower()

import os

@pytest.mark.smoke
def test_llm_engine_connection():
    """Verify that the configured LLM engine can generate a response.
    This will actually hit the configured backend to ensure API keys work (unless in CI)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("Skipping real network LLM call in GitHub Actions CI.")
        
    engine = LLMEngine()
    response = engine.generate("Reply exactly with the word PONG.", max_tokens=10)
    assert isinstance(response, str)
    assert "pong" in response.lower()
