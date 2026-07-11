"""
Skill Router - Command Classification and Routing

Routes user commands to appropriate skills or general LLM.
"""

import logging
import os
import re
from typing import Optional, Dict, Callable, Any
from enum import Enum

from core_logic.error_handler import SkillException

logger = logging.getLogger(__name__)


class SkillType(Enum):
    """Types of skills."""
    TIME = "time"
    CALCULATOR = "calculator"
    REMINDER = "reminder"
    WEATHER = "weather"
    DOCUMENT = "document"
    SYSTEM = "system"
    FILE = "file"
    C_INTEGRATION = "c_integration"
    SEARCH = "search"
    AGENT = "agent"
    BUILD = "build"
    WEB_BUILD = "web_build"
    MESSAGING = "messaging"
    BRIEFING = "briefing"
    GENERAL = "general"


class SkillRouter:
    """Route commands to appropriate skills."""
    
    # Keyword patterns for skill matching
    PATTERNS = {
        SkillType.TIME: [
            r"what\s+time",
            r"what\'s\s+the\s+time",
            r"current\s+time",
            r"tell\s+me\s+the\s+time",
            r"what\s+hour",
        ],
        SkillType.CALCULATOR: [
            r"calculate",
            r"what\s+is\s+(\d+)\s*[\+\-\*\/]\s*(\d+)",
            r"(\d+)\s*[\+\-\*\/]\s*(\d+)",
            r"solve",
            r"math",
        ],
        SkillType.REMINDER: [
            r"remind\s+me",
            r"set\s+a\s+reminder",
            r"set\s+timer",
            r"alarm",
        ],
        SkillType.WEATHER: [
            r"weather",
            r"temperature",
            r"forecast",
            r"rain",
            r"sunny",
        ],
        SkillType.DOCUMENT: [
            r"\bsummari[sz]e\b",
            r"\bsummary\s+of\b",
            r"\btl;?dr\b",
            r"\bgive\s+me\s+a\s+summary\b",
            r"read\s+(?:file|document|pdf)",
            r"open\s+(?:file|document|pdf)",
            r"show\s+(?:file|document|pdf)",
            r"extract\s+text",
            r"search\s+(?:in|inside)\s+(?:the\s+)?(?:file|document|pdf)",
            r"search\s+(?:for\s+)?\".+\"\s+in\s+",
            r"\bpdf\b",
        ],
        SkillType.SYSTEM: [
            r"cpu",
            r"memory",
            r"\bprocess(?:es)?\b",
            r"disk",
            r"system\s+info",
            r"show\s+(?:cpu|memory|disk)",
            r"check\s+(?:cpu|memory|disk)",
            r"\bram\b",
            r"usage",
        ],
        SkillType.FILE: [
            r"\blist\s+(?:file|files)\b",
            r"\bsearch\s+(?:file|files)\b",
            r"\bfind\s+(?:file|files)\b",
            r"\bshow\s+directory\b",
            r"^\s*ls\s+",
            r"\bdirectory\b",
            r"\bfolder\b",
        ],
        SkillType.C_INTEGRATION: [
            r"compile\s+",
            r"run\s+(?:program|file)",
            r"execute\s+(?:c|program)",
            r"\.c\s+",
        ],
        # Agent routing — explicit persona agent requests.
        # Must appear BEFORE BUILD to intercept "ask cipher..." etc.
        SkillType.AGENT: [
            r"\bask\s+(?:nova|cipher|forge)\b",
            r"\btell\s+(?:nova|cipher|forge)\b",
            r"\bhey\s+(?:nova|cipher|forge)\b",
            r"\b(?:nova|cipher|forge)[,:]?\s+",
            r"\bask\s+\w+\s+(?:for|about|to)\s+(?:the\s+)?(?:memory|valgrind|compile|compilation|print|document|pdf|system)",
        ],
        # Build / coding tasks → multi-agent orchestrator.
        # Must appear BEFORE the SEARCH catch-all.
        SkillType.BUILD: [
            r"\bbuild\s+(?:me\s+)?(?:a|an|the)\b",
            r"\bcreate\s+(?:a|an|the)?\s*(?:project|app|program|script|api|tool|module|package)\b",
            r"\bwrite\s+(?:a|an|the)?\s*(?:program|script|app|application|module|function|class)\b",
            r"\bwrite\s+(?:a|an)\s+\w+\s+(?:that|which|to)\b",
            r"\bmake\s+(?:a|an|the)?\s*(?:project|app|program|script|api|tool)\b",
            r"\bcode\s+(?:a|an|the)?\s*",
            r"\bdevelop\s+(?:a|an|the)?\s*",
            r"\bscaffold\b",
            r"\bgenerate\s+(?:a|an|the)?\s*(?:project|app|code|script|program)\b",
            r"\bimplement\s+(?:a|an|the)?\s*",
        ],
        # Website building — routed to the WebBuilderSkill + Orchestrator.
        SkillType.WEB_BUILD: [
            r"\bbuild\s+(?:me\s+)?(?:a|an|the)?\s*(?:website|webpage|web\s*page|web\s*site|landing\s*page|portfolio|blog\s*site)\b",
            r"\bcreate\s+(?:a|an|the)?\s*(?:website|webpage|web\s*page|web\s*site|landing\s*page|portfolio|blog\s*site)\b",
            r"\bmake\s+(?:me\s+)?(?:a|an|the)?\s*(?:website|webpage|web\s*page|web\s*site|landing\s*page|portfolio)\b",
            r"\bdesign\s+(?:a|an|the)?\s*(?:website|webpage|web\s*page|web\s*site|landing\s*page)\b",
            r"\bgenerate\s+(?:a|an|the)?\s*(?:website|webpage|web\s*page|landing\s*page)\b",
        ],
        # Messaging — send emails and WhatsApp messages.
        SkillType.MESSAGING: [
            r"\bsend\s+(?:a\s+)?(?:message|email|text|mail)\b",
            r"\bemail\s+\w+\b",
            r"\bwhatsapp\s+\w+\b",
            r"\btext\s+\w+\s+(?:that|saying)\b",
            r"\bmessage\s+\w+\s+(?:that|saying)\b",
            r"\btell\s+\w+\s+(?:that|to\s+)\b",
            r"\bsend\s+(?:to|an?)\s+\w+\b",
        ],
        # Status briefing — triggers the J.A.R.V.I.S. narrative engine.
        # Must appear BEFORE the SEARCH catch-all.
        SkillType.BRIEFING: [
            r"\bstatus\s+(?:update|report|briefing)\b",
            r"\bgive\s+me\s+(?:a\s+)?(?:status|update|rundown|briefing|overview|sitrep)\b",
            r"\bhow(?:'?s)?\s+(?:the\s+)?(?:app|project|system|everything)\s+doing\b",
            r"\bhow\s+are\s+(?:things|we)\s+(?:doing|going|looking)\b",
            r"\bmorning\s+(?:briefing|report|update)\b",
            r"\bevening\s+(?:briefing|report|update)\b",
            r"\bwake\s+up\b",
            r"\bwhat(?:'?s)?\s+the\s+(?:status|situation|state)\b",
            r"\brundown\b",
            r"\bsitrep\b",
            r"\bbriefing\b",
            r"\bbring\s+me\s+up\s+to\s+(?:speed|date)\b",
            r"\bany(?:thing)?\s+(?:new|I\s+should\s+know)\b",
            # Email / inbox queries
            r"\bemail(?:s)?\b",
            r"\binbox\b",
            r"\bunread\b",
            r"\bmail(?:s|box)?\b",
            r"\bcheck\s+(?:my\s+)?(?:email|inbox|mail)\b",
            r"\bshow\s+(?:my\s+)?(?:email|inbox|mail)\b",
            r"\bhow\s+many\s+(?:email|message)s?\b",
            r"\bany\s+(?:new\s+)?(?:email|message|mail)s?\b",
            # GitHub / PR queries
            r"\bgithub\b",
            r"\bpull\s+request(?:s)?\b",
            r"\b(?:open\s+)?pr(?:s)?\b",
            r"\bcheck\s+(?:the\s+)?(?:repo|repository|github|prs?)\b",
            r"\bhow\s+many\s+(?:pr|pull\s+request)s?\b",
        ],
        # Catch-all research/web-search routed to Gemini.
        # Listed LAST so explicit skills (time/calculator/system/...) win first.
        SkillType.SEARCH: [
            r"^\s*search\b",
            r"^\s*google\b",
            r"^\s*look\s+up\b",
            r"\bask\s+gemini\b",
            r"\buse\s+gemini\b",
            r"^\s*hey\s+gemini\b",
            r"^\s*who\s+(?:is|was|are)\b",
            r"^\s*what\s+(?:is|are|'?s)\b",
            r"^\s*tell\s+me\s+about\b",
            r"^\s*explain\b",
            r"^\s*define\b",
            r"^\s*why\s+(?:is|does|do|are)\b",
            r"^\s*when\s+(?:did|is|was|will)\b",
            r"^\s*where\s+(?:is|are|was)\b",
            r"^\s*how\s+(?:does|do|is|to|can|did)\b",
            # News / current events — must route to search, NOT general LLM
            r"\bnews\b",
            r"\bheadlines?\b",
            r"\blatest\b",
            r"\bhappening\b",
            r"\btrending\b",
            r"\bcurrent\s+events?\b",
            r"\btoday'?s?\s+(?:news|headlines|events|updates)\b",
            r"\bshow\s+me\s+(?:the\s+)?(?:news|latest|headlines)\b",
            r"\bwhat(?:'?s)?\s+(?:new|happening|going\s+on)\b",
            r"\bany\s+(?:news|updates)\b",
            r"\brecent\s+(?:news|events|updates)\b",
            r"\bwhat\s+happened\b",
        ],
    }
    
    def __init__(self):
        """Initialize skill router with handlers."""
        self.skills: Dict[SkillType, Callable] = {}
        self._register_default_skills()
    
    def _register_default_skills(self) -> None:
        """Register built-in skills."""
        # Phase 3 skills
        self.register_skill(SkillType.TIME, self._handle_time)
        self.register_skill(SkillType.CALCULATOR, self._handle_calculator)
        self.register_skill(SkillType.REMINDER, self._handle_reminder)
        self.register_skill(SkillType.WEATHER, self._handle_weather)
        
        # Phase 4 skills
        try:
            from skills.document_skill import DocumentSkill
            self.register_skill(SkillType.DOCUMENT, DocumentSkill.handle)
            logger.info("✅ Registered document skill")
        except Exception as e:
            logger.warning(f"Could not load document skill: {str(e)}")
        
        try:
            from skills.system_skill import SystemSkill
            self.register_skill(SkillType.SYSTEM, SystemSkill.handle)
            logger.info("✅ Registered system skill")
        except Exception as e:
            logger.warning(f"Could not load system skill: {str(e)}")
        
        try:
            from skills.file_skill import FileSkill
            self.register_skill(SkillType.FILE, FileSkill.handle)
            logger.info("✅ Registered file skill")
        except Exception as e:
            logger.warning(f"Could not load file skill: {str(e)}")
        
        try:
            from skills.c_integration_skill import CIntegrationSkill
            self.register_skill(SkillType.C_INTEGRATION, CIntegrationSkill.handle)
            logger.info("✅ Registered C integration skill")
        except Exception as e:
            logger.warning(f"Could not load C integration skill: {str(e)}")
        
        # Search backend: duckduckgo (OSS, free, default) or gemini (cloud).
        search_engine = os.getenv("SEARCH_ENGINE", "duckduckgo").lower()
        try:
            if search_engine == "gemini":
                from skills.gemini_search_skill import GeminiSearchSkill
                self.register_skill(SkillType.SEARCH, GeminiSearchSkill.handle)
                logger.info("✅ Registered Gemini search skill")
            else:
                from skills.web_search_skill import WebSearchSkill
                self.register_skill(SkillType.SEARCH, WebSearchSkill.handle)
                logger.info("✅ Registered DuckDuckGo + Ollama search skill")
        except Exception as e:
            logger.warning(f"Could not load search skill: {str(e)}")

        logger.info("Registered skills (Phase 3 + Phase 4 + Search)")

        # Phase 7 skills
        try:
            from skills.web_builder_skill import WebBuilderSkill
            self.register_skill(SkillType.WEB_BUILD, WebBuilderSkill.handle)
            logger.info("✅ Registered web builder skill")
        except Exception as e:
            logger.warning(f"Could not load web builder skill: {str(e)}")

        try:
            from skills.messaging_skill import MessagingSkill
            self.register_skill(SkillType.MESSAGING, MessagingSkill.handle)
            logger.info("✅ Registered messaging skill")
        except Exception as e:
            logger.warning(f"Could not load messaging skill: {str(e)}")
    
    def register_skill(
        self,
        skill_type: SkillType,
        handler: Callable[[str], str]
    ) -> None:
        """
        Register a skill handler.
        
        Args:
            skill_type: Type of skill
            handler: Function that takes command text and returns response
        """
        self.skills[skill_type] = handler
        logger.info(f"Registered skill: {skill_type.value}")
    
    def classify_command(self, text: str) -> SkillType:
        """
        Classify command to determine which skill to use.
        
        Args:
            text: User command text
            
        Returns:
            SkillType (or GENERAL if no match)
        """
        text_lower = text.lower()
        
        # Check each skill pattern
        for skill_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    logger.debug(f"Matched {skill_type.value}: {pattern}")
                    return skill_type
        
        # Default to general LLM
        return SkillType.GENERAL
    
    def execute_command(self, text: str) -> Optional[str]:
        """
        Execute command by routing to appropriate skill.
        
        Args:
            text: User command text
            
        Returns:
            Response string, or None if should go to general LLM
        """
        skill_type = self.classify_command(text)
        
        if skill_type == SkillType.GENERAL:
            return None  # Let LLM handle it
        
        try:
            if skill_type not in self.skills:
                logger.warning(f"Skill {skill_type.value} not registered")
                return None
            
            handler = self.skills[skill_type]
            response = handler(text)
            
            logger.info(f"Executed {skill_type.value} skill")
            return response
        
        except Exception as e:
            logger.error(f"Skill execution failed: {str(e)}")
            raise SkillException(f"Skill {skill_type.value} failed: {str(e)}")
    
    # Built-in skill handlers
    
    def _handle_time(self, text: str) -> str:
        """Handle time-related queries."""
        from datetime import datetime
        
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d, %Y")
        
        return f"The current time is {time_str} on {date_str}."
    
    def _handle_calculator(self, text: str) -> str:
        """Handle calculator queries."""
        # Try to extract and solve math expression
        match = re.search(r'(\d+)\s*([\+\-\*\/])\s*(\d+)', text.lower())
        
        if not match:
            return "I couldn't parse the calculation. Please provide two numbers and an operator."
        
        try:
            num1 = float(match.group(1))
            op = match.group(2)
            num2 = float(match.group(3))
            
            if op == '+':
                result = num1 + num2
            elif op == '-':
                result = num1 - num2
            elif op == '*':
                result = num1 * num2
            elif op == '/':
                if num2 == 0:
                    return "Cannot divide by zero."
                result = num1 / num2
            else:
                return "Unknown operation."
            
            # Format result
            if result == int(result):
                result_str = str(int(result))
            else:
                result_str = f"{result:.2f}"
            
            return f"{num1} {op} {num2} equals {result_str}"
        
        except Exception as e:
            logger.error(f"Calculator error: {str(e)}")
            return f"Calculation failed: {str(e)}"
    
    def _handle_reminder(self, text: str) -> str:
        """Handle reminder/timer queries."""
        # Extract time from text
        match = re.search(r'(\d+)\s*(seconds?|minutes?|hours?)', text.lower())
        
        if not match:
            return "I could set a reminder, but you'll need to implement the reminder service first."
        
        time_value = match.group(1)
        time_unit = match.group(2)
        
        return f"I would set a reminder for {time_value} {time_unit}. Reminder service not yet implemented."
    
    def _handle_weather(self, text: str) -> str:
        """Handle weather queries."""
        # Check if asking about current location or specific place
        match = re.search(r'(in|at|for)\s+(\w+)', text.lower())
        location = match.group(2) if match else "your location"
        
        return f"I would check the weather for {location}, but the weather service is not yet integrated."
    
    def get_available_skills(self) -> Dict[str, list]:
        """Get list of available skills and keywords."""
        return {
            skill_type.value: patterns
            for skill_type, patterns in self.PATTERNS.items()
        }
