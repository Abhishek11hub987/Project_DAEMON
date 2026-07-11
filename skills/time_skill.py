"""
Time Skill - Handle time and date queries
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class TimeSkill:
    """Handle time-related queries."""
    
    @staticmethod
    def get_current_time() -> str:
        """Get current time as string."""
        now = datetime.now()
        return now.strftime("%H:%M:%S")
    
    @staticmethod
    def get_current_date() -> str:
        """Get current date as string."""
        now = datetime.now()
        return now.strftime("%A, %B %d, %Y")
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle time-related query.
        
        Args:
            query: User query
            
        Returns:
            Response string
        """
        query_lower = query.lower()
        
        # What time is it?
        if any(x in query_lower for x in ["time", "hour", "minute"]):
            time_str = TimeSkill.get_current_time()
            date_str = TimeSkill.get_current_date()
            return f"The current time is {time_str} on {date_str}."
        
        # What date is it?
        if any(x in query_lower for x in ["date", "day", "today"]):
            date_str = TimeSkill.get_current_date()
            return f"Today is {date_str}."
        
        # Fallback
        return f"Time: {TimeSkill.get_current_time()}, Date: {TimeSkill.get_current_date()}"
