"""
Conversation Memory System

Maintains conversation history with:
- Multi-turn context
- Automatic cleanup
- Persistence
- Token tracking
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from core_logic.config import Config

logger = logging.getLogger(__name__)


class ConversationTurn:
    """Single turn in a conversation."""
    
    def __init__(self, role: str, content: str, timestamp: Optional[datetime] = None):
        """
        Initialize a conversation turn.
        
        Args:
            role: "user" or "assistant"
            content: Text content
            timestamp: When this turn occurred (defaults to now)
        """
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat()
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ConversationTurn":
        """Create from dictionary."""
        timestamp = datetime.fromisoformat(data["timestamp"])
        return ConversationTurn(data["role"], data["content"], timestamp)
    
    def token_count(self) -> int:
        """Rough estimate of tokens (4 chars ≈ 1 token)."""
        return len(self.content) // 4


class ConversationMemory:
    """Manage conversation history and context."""
    
    def __init__(
        self,
        conversation_id: str = "default",
        max_turns: int = 20,
        max_tokens: int = 4000,
        storage_path: Optional[str] = None
    ):
        """
        Initialize conversation memory.
        
        Args:
            conversation_id: Unique conversation identifier
            max_turns: Maximum turns to keep in memory
            max_tokens: Maximum tokens in context window
            storage_path: Path to store conversation history
        """
        self.conversation_id = conversation_id
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        
        # Setup storage
        if storage_path is None:
            storage_path = os.path.join(Config.LOGS_DIR, "conversations")
        
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Conversation history
        self.turns: List[ConversationTurn] = []
        self.created_at = datetime.now()
        
        # Load existing conversation if available
        self._load_conversation()
        
        logger.info(f"Memory initialized: {conversation_id} ({len(self.turns)} turns)")
    
    def add_turn(self, role: str, content: str) -> None:
        """
        Add a turn to the conversation.
        
        Args:
            role: "user" or "assistant"
            content: Text content
        """
        turn = ConversationTurn(role, content)
        self.turns.append(turn)
        
        logger.debug(f"Added {role}: {content[:50]}...")
        
        # Trim if necessary
        self._trim_context()
        
        # Persist to disk
        self._save_conversation()
    
    def _trim_context(self) -> None:
        """Trim context to fit within max_turns and max_tokens."""
        # First: trim by turn count
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]
            logger.info(f"Trimmed to max_turns: {len(self.turns)}")
        
        # Second: trim by token count
        total_tokens = sum(turn.token_count() for turn in self.turns)
        
        while total_tokens > self.max_tokens and len(self.turns) > 1:
            removed = self.turns.pop(0)
            total_tokens -= removed.token_count()
            logger.info(f"Trimmed token overflow: {total_tokens}/{self.max_tokens}")
    
    def get_context(self) -> List[Dict[str, str]]:
        """
        Get conversation context as LLM message format.
        
        Returns:
            List of {"role": "user"/"assistant", "content": "..."}
        """
        return [turn.to_dict() for turn in self.turns]
    
    def get_system_context(self) -> str:
        """
        Get conversation summary for system prompt.
        
        Returns:
            Text summary of recent conversation
        """
        if not self.turns:
            return "No previous conversation history."
        
        # Show last 3 turns
        recent = self.turns[-6:]
        summary = "Recent conversation:\n"
        for turn in recent:
            role = "User" if turn.role == "user" else "D.A.E.M.O.N."
            summary += f"{role}: {turn.content}\n"
        
        return summary
    
    def clear(self) -> None:
        """Clear conversation history."""
        self.turns.clear()
        self._save_conversation()
        logger.info(f"Cleared memory for {self.conversation_id}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get conversation statistics."""
        total_tokens = sum(turn.token_count() for turn in self.turns)
        
        user_turns = [t for t in self.turns if t.role == "user"]
        assistant_turns = [t for t in self.turns if t.role == "assistant"]
        
        return {
            "conversation_id": self.conversation_id,
            "total_turns": len(self.turns),
            "user_messages": len(user_turns),
            "assistant_messages": len(assistant_turns),
            "total_tokens": total_tokens,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.turns[-1].timestamp.isoformat() if self.turns else None
        }
    
    def _get_storage_file(self) -> Path:
        """Get the storage file path for this conversation."""
        filename = f"{self.conversation_id}.json"
        return self.storage_path / filename
    
    def _save_conversation(self) -> None:
        """Save conversation to disk."""
        try:
            storage_file = self._get_storage_file()
            
            data = {
                "conversation_id": self.conversation_id,
                "created_at": self.created_at.isoformat(),
                "turns": [turn.to_dict() for turn in self.turns]
            }
            
            with open(storage_file, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.debug(f"Saved conversation to {storage_file}")
        
        except Exception as e:
            logger.error(f"Failed to save conversation: {str(e)}")
    
    def _load_conversation(self) -> None:
        """Load conversation from disk if it exists."""
        try:
            storage_file = self._get_storage_file()
            
            if not storage_file.exists():
                return
            
            with open(storage_file, "r") as f:
                data = json.load(f)
            
            self.created_at = datetime.fromisoformat(data["created_at"])
            self.turns = [ConversationTurn.from_dict(turn) for turn in data.get("turns", [])]
            
            logger.info(f"Loaded {len(self.turns)} turns from disk")
        
        except Exception as e:
            logger.warning(f"Failed to load conversation: {str(e)}")


class MultiUserMemory:
    """Manage multiple conversations for different users."""
    
    def __init__(self, storage_path: Optional[str] = None):
        """Initialize multi-user memory system."""
        self.conversations: Dict[str, ConversationMemory] = {}
        self.storage_path = storage_path
    
    def get_conversation(self, user_id: str = "default") -> ConversationMemory:
        """
        Get or create conversation for a user.
        
        Args:
            user_id: Unique user identifier
            
        Returns:
            ConversationMemory instance
        """
        if user_id not in self.conversations:
            self.conversations[user_id] = ConversationMemory(
                conversation_id=user_id,
                storage_path=self.storage_path
            )
        
        return self.conversations[user_id]
    
    def cleanup_old_conversations(self, days: int = 7) -> None:
        """
        Remove conversation files older than specified days.
        
        Args:
            days: Remove conversations older than this many days
        """
        if not self.storage_path:
            return
        
        storage_path = Path(self.storage_path)
        cutoff = datetime.now() - timedelta(days=days)
        
        for file in storage_path.glob("*.json"):
            if file.stat().st_mtime < cutoff.timestamp():
                file.unlink()
                logger.info(f"Deleted old conversation: {file.name}")
