import pytest
from unittest.mock import patch
from core_logic.main import DAEMON

@pytest.mark.e2e
@patch("core_logic.llm_engine.LLMEngine.generate")
@patch("core_logic.config.Config.LLM_BACKEND", "groq")
@patch("core_logic.config.Config.GROQ_API_KEY", "dummy_key")
def test_full_text_pipeline(mock_generate):
    """
    End-to-end test simulating a user passing a text command.
    We mock the LLMEngine so it doesn't consume real tokens or require API keys in CI.
    We patch backend to groq and provide a dummy key so initialization succeeds without real network requests.
    """
    # Setup mock to return a predictable response
    mock_generate.return_value = "The time is currently 12:00 PM."

    # Initialize the DAEMON
    daemon = DAEMON(user_id="test_user")

    # The router might intercept "what time is it" without hitting the LLM if it's a native skill.
    # Let's send a generic conversational message to ensure the LLM is hit.
    response = daemon.process_command("Hello, Daemon, how are you?")

    # Verify the mock was called since this isn't a native skill
    assert mock_generate.called
    assert "12:00 PM" in response
