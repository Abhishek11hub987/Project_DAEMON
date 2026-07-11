"""
Reminder Skill - Handle reminders and timers
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ReminderSkill:
    """Handle reminders and timers."""
    
    # Store active reminders (in production, would use proper scheduler)
    active_reminders = []
    
    TIME_UNITS = {
        'second': 1,
        'seconds': 1,
        'minute': 60,
        'minutes': 60,
        'hour': 3600,
        'hours': 3600,
    }
    
    @staticmethod
    def parse_reminder(text: str) -> Optional[Tuple[int, str, str]]:
        """
        Parse reminder from text.
        
        Args:
            text: User input
            
        Returns:
            (duration_seconds, unit, message) or None
        """
        # Pattern: remind me in 5 minutes [to do something]
        pattern = r'(?:remind\s+me\s+)?(?:in|set|to)\s+(\d+)\s+(second|minute|hour)s?(?:\s+(?:to|that)\s+(.+))?'
        match = re.search(pattern, text.lower())
        
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            message = match.group(3) or "Reminder"
            
            unit_seconds = ReminderSkill.TIME_UNITS.get(unit, 1)
            duration_seconds = amount * unit_seconds
            
            return (duration_seconds, unit, message)
        
        return None
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle reminder query.
        
        Args:
            query: User query
            
        Returns:
            Response string
        """
        parsed = ReminderSkill.parse_reminder(query)
        
        if not parsed:
            return "I can set reminders like 'remind me in 5 minutes' or 'remind me in 2 hours to take a break'."
        
        duration, unit, message = parsed
        
        # In production, would use APScheduler or similar
        # For now, just acknowledge
        return f"I would set a reminder to '{message}' in {duration} seconds. Reminder service coming soon!"
