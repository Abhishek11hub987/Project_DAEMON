"""Base Skill Class"""
from abc import ABC, abstractmethod
from typing import Any
import logging

logger = logging.getLogger(__name__)

class BaseSkill(ABC):
    """Abstract base class for all D.A.E.M.O.N. skills."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.enabled = True
    
    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the skill."""
        pass
    
    @abstractmethod
    def get_keywords(self) -> list:
        """Get voice command keywords that trigger this skill."""
        pass
    
    def is_applicable(self, user_input: str) -> bool:
        """Check if this skill applies to user input."""
        keywords = self.get_keywords()
        return any(keyword.lower() in user_input.lower() for keyword in keywords)
