"""Error handling and recovery"""
import logging
from typing import Callable, Any, Optional
from functools import wraps

class DAEMONException(Exception):
    """Base exception for D.A.E.M.O.N."""
    pass

class AudioException(DAEMONException):
    pass

class WakeWordException(DAEMONException):
    pass

class LLMException(DAEMONException):
    pass

class SkillException(DAEMONException):
    pass

class SystemException(DAEMONException):
    pass

class SandboxSecurityError(DAEMONException):
    """Raised when a workspace operation violates security boundaries."""
    pass

class OrchestratorException(DAEMONException):
    """Raised when the multi-agent orchestration loop fails."""
    pass

def retry_on_failure(max_attempts: int = 3, delay: float = 1.0):
    """Decorator to retry a function on failure."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import time
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    logging.warning(f"Attempt {attempt}/{max_attempts} failed. Retrying...")
                    time.sleep(delay)
        return wrapper
    return decorator

def handle_exception(exc: Exception, context: str = "") -> str:
    """Handle exceptions gracefully."""
    logger = logging.getLogger("daemon_error")
    logger.error(f"{context}: {str(exc)}", exc_info=True)
    
    error_map = {
        AudioException: "I'm having trouble with my microphone.",
        WakeWordException: "I didn't catch the wake word. Try again.",
        LLMException: "I'm unable to process your request right now.",
        SkillException: "The requested task failed.",
        SystemException: "A system error occurred.",
        SandboxSecurityError: "That operation was blocked for security reasons.",
        OrchestratorException: "The multi-agent task pipeline encountered a failure.",
    }
    
    for exc_type, msg in error_map.items():
        if isinstance(exc, exc_type):
            return msg
    return "An unexpected error occurred."
