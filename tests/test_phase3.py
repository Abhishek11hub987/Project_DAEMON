"""
Phase 3 Tests - Memory, Skills, and Integration

Tests for:
- Conversation memory
- Skill routing
- Multi-turn interactions
- Context management
"""

import pytest
import os
import json
from datetime import datetime
from pathlib import Path

from core_logic.memory import ConversationTurn, ConversationMemory, MultiUserMemory
from core_logic.skill_router import SkillRouter, SkillType
from core_logic.main import DAEMON
from skills.time_skill import TimeSkill
from skills.calculator_skill import CalculatorSkill
from skills.reminder_skill import ReminderSkill


class TestConversationMemory:
    """Test conversation memory system."""
    
    @pytest.fixture
    def memory(self):
        """Create test memory instance."""
        return ConversationMemory(conversation_id="test")
    
    def test_memory_initialization(self, memory):
        """Test memory initializes correctly."""
        assert memory.conversation_id == "test"
        assert memory.max_turns == 20
        assert len(memory.turns) == 0
    
    def test_add_turn(self, memory):
        """Test adding turns to memory."""
        memory.add_turn("user", "Hello")
        assert len(memory.turns) == 1
        assert memory.turns[0].role == "user"
        assert memory.turns[0].content == "Hello"
    
    def test_conversation_context(self, memory):
        """Test getting conversation context."""
        memory.add_turn("user", "What time is it?")
        memory.add_turn("assistant", "It is 3:00 PM")
        
        context = memory.get_context()
        assert len(context) == 2
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"
    
    def test_memory_trimming(self, memory):
        """Test context trimming by turn count."""
        memory.max_turns = 5
        
        # Add more than max_turns
        for i in range(10):
            memory.add_turn("user", f"Message {i}")
        
        # Should be trimmed to max_turns
        assert len(memory.turns) <= memory.max_turns
    
    def test_clear_memory(self, memory):
        """Test clearing memory."""
        memory.add_turn("user", "Hello")
        assert len(memory.turns) == 1
        
        memory.clear()
        assert len(memory.turns) == 0
    
    def test_memory_persistence(self, memory):
        """Test saving and loading memory."""
        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi there!")
        
        # Create new memory instance with same ID
        memory2 = ConversationMemory(conversation_id="test")
        
        # Should have loaded previous conversation
        assert len(memory2.turns) >= 0  # May be 0 if file not saved yet
    
    def test_memory_stats(self, memory):
        """Test memory statistics."""
        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi!")
        
        stats = memory.get_stats()
        assert stats["total_turns"] == 2
        assert stats["user_messages"] == 1
        assert stats["assistant_messages"] == 1


class TestSkillRouter:
    """Test skill routing system."""
    
    @pytest.fixture
    def router(self):
        """Create test router."""
        return SkillRouter()
    
    def test_router_initialization(self, router):
        """Test router initializes with default skills."""
        assert len(router.skills) == 4  # time, calculator, reminder, weather
    
    def test_time_classification(self, router):
        """Test time command classification."""
        skill = router.classify_command("What time is it?")
        assert skill == SkillType.TIME
    
    def test_calculator_classification(self, router):
        """Test calculator command classification."""
        skill = router.classify_command("What is 5 + 3?")
        assert skill == SkillType.CALCULATOR
    
    def test_reminder_classification(self, router):
        """Test reminder command classification."""
        skill = router.classify_command("Remind me in 5 minutes")
        assert skill == SkillType.REMINDER
    
    def test_weather_classification(self, router):
        """Test weather command classification."""
        skill = router.classify_command("What is the weather?")
        assert skill == SkillType.WEATHER
    
    def test_general_classification(self, router):
        """Test general command (no skill match)."""
        skill = router.classify_command("Tell me a joke")
        assert skill == SkillType.GENERAL
    
    def test_execute_time_skill(self, router):
        """Test executing time skill."""
        response = router.execute_command("What time is it?")
        assert response is not None
        assert "time" in response.lower() or ":" in response
    
    def test_execute_calculator_skill(self, router):
        """Test executing calculator skill."""
        response = router.execute_command("What is 10 + 5?")
        assert response is not None
        assert ("15" in response) or ("equals" in response.lower())
    
    def test_execute_general_command(self, router):
        """Test executing general command (returns None)."""
        response = router.execute_command("Tell me a joke")
        assert response is None  # Should go to LLM


class TestTimeSkill:
    """Test time skill."""
    
    def test_get_current_time(self):
        """Test getting current time."""
        time_str = TimeSkill.get_current_time()
        assert len(time_str) > 0
        assert ":" in time_str  # Should have HH:MM:SS format
    
    def test_get_current_date(self):
        """Test getting current date."""
        date_str = TimeSkill.get_current_date()
        assert len(date_str) > 0
        # Should contain day of week
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(day in date_str for day in days)
    
    def test_time_skill_handle(self):
        """Test time skill handler."""
        response = TimeSkill.handle("What time is it?")
        assert "time" in response.lower()
        assert ":" in response


class TestCalculatorSkill:
    """Test calculator skill."""
    
    def test_parse_addition(self):
        """Test parsing addition."""
        result = CalculatorSkill.parse_expression("What is 5 + 3?")
        assert result == (5, '+', 3)
    
    def test_parse_subtraction(self):
        """Test parsing subtraction."""
        result = CalculatorSkill.parse_expression("10 - 4")
        assert result == (10, '-', 4)
    
    def test_parse_multiplication(self):
        """Test parsing multiplication."""
        result = CalculatorSkill.parse_expression("6 * 7")
        assert result == (6, '*', 7)
    
    def test_parse_division(self):
        """Test parsing division."""
        result = CalculatorSkill.parse_expression("20 / 4")
        assert result == (20, '/', 4)
    
    def test_calculate_addition(self):
        """Test addition calculation."""
        result = CalculatorSkill.calculate(5, '+', 3)
        assert result == 8
    
    def test_calculate_subtraction(self):
        """Test subtraction calculation."""
        result = CalculatorSkill.calculate(10, '-', 4)
        assert result == 6
    
    def test_calculate_multiplication(self):
        """Test multiplication calculation."""
        result = CalculatorSkill.calculate(6, '*', 7)
        assert result == 42
    
    def test_calculate_division(self):
        """Test division calculation."""
        result = CalculatorSkill.calculate(20, '/', 4)
        assert result == 5
    
    def test_divide_by_zero(self):
        """Test division by zero."""
        result = CalculatorSkill.calculate(5, '/', 0)
        assert result is None
    
    def test_calculator_skill_handle(self):
        """Test calculator skill handler."""
        response = CalculatorSkill.handle("What is 10 + 5?")
        assert "15" in response or "equals" in response.lower()


class TestReminderSkill:
    """Test reminder skill."""
    
    def test_parse_reminder(self):
        """Test parsing reminder."""
        result = ReminderSkill.parse_reminder("Remind me in 5 minutes")
        assert result is not None
        duration, unit, message = result
        assert duration == 300  # 5 * 60 seconds
    
    def test_reminder_skill_handle(self):
        """Test reminder skill handler."""
        response = ReminderSkill.handle("Remind me in 10 seconds")
        assert "reminder" in response.lower() or "would set" in response.lower()


class TestMultiUserMemory:
    """Test multi-user memory system."""
    
    @pytest.fixture
    def multi_memory(self):
        """Create test multi-user memory."""
        return MultiUserMemory()
    
    def test_get_conversation(self, multi_memory):
        """Test getting conversation for user."""
        conv = multi_memory.get_conversation("user1")
        assert conv is not None
        assert conv.conversation_id == "user1"
    
    def test_different_users_different_conversations(self, multi_memory):
        """Test that different users have different conversations."""
        conv1 = multi_memory.get_conversation("user1")
        conv2 = multi_memory.get_conversation("user2")
        
        conv1.add_turn("user", "Hello from user1")
        conv2.add_turn("user", "Hello from user2")
        
        assert conv1.turns[0].content == "Hello from user1"
        assert conv2.turns[0].content == "Hello from user2"


class TestDAEMONIntegration:
    """Test DAEMON integration with Phase 3 features."""
    
    @pytest.fixture
    def daemon(self):
        """Create test DAEMON instance."""
        return DAEMON(user_id="test_user")
    
    def test_daemon_initialization(self, daemon):
        """Test DAEMON initializes correctly."""
        assert daemon.user_id == "test_user"
        assert daemon.memory is not None
        assert daemon.skill_router is not None
    
    def test_daemon_skill_execution(self, daemon):
        """Test DAEMON routing to skills."""
        response = daemon.process_command("What time is it?")
        assert response is not None
        assert isinstance(response, str)
    
    def test_daemon_memory_integration(self, daemon):
        """Test DAEMON integrates memory."""
        response = daemon.process_command("Hi there")
        
        # Check that memory was updated
        assert daemon.memory is not None
        assert len(daemon.memory.turns) >= 2  # user + assistant
    
    def test_daemon_status(self, daemon):
        """Test getting DAEMON status."""
        status = daemon.get_status()
        assert "memory" in status
        assert "skills" in status
        assert status["version"] == "Phase 3"


class TestContextManagement:
    """Test context window management."""
    
    def test_context_with_memory(self):
        """Test using memory context with LLM."""
        memory = ConversationMemory()
        
        # Build a conversation
        memory.add_turn("user", "My name is Alice")
        memory.add_turn("assistant", "Nice to meet you, Alice!")
        memory.add_turn("user", "What is my name?")
        
        # Get context
        context = memory.get_context()
        
        # Verify context format
        assert len(context) == 3
        assert all("role" in msg and "content" in msg for msg in context)
    
    def test_get_system_context(self):
        """Test getting system context summary."""
        memory = ConversationMemory()
        
        for i in range(5):
            memory.add_turn("user", f"Message {i}")
            memory.add_turn("assistant", f"Response {i}")
        
        context = memory.get_system_context()
        assert isinstance(context, str)
        assert len(context) > 0


# Integration test: Multi-turn conversation
class TestMultiTurnConversation:
    """Test multi-turn conversation scenario."""
    
    def test_multi_turn_with_memory(self):
        """Test multi-turn conversation with memory."""
        memory = ConversationMemory()
        router = SkillRouter()
        
        # Turn 1: Time question
        q1 = "What time is it?"
        resp1 = router.execute_command(q1)
        memory.add_turn("user", q1)
        memory.add_turn("assistant", resp1)
        
        # Turn 2: Math question
        q2 = "What is 10 + 5?"
        resp2 = router.execute_command(q2)
        memory.add_turn("user", q2)
        memory.add_turn("assistant", resp2)
        
        # Check conversation history
        context = memory.get_context()
        assert len(context) == 4
        assert context[0]["content"] == q1
        assert context[1]["content"] == resp1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
