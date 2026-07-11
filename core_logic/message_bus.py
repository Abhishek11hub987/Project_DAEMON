"""Thread-safe message bus for UI updates."""
import logging
import threading
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class MessageBus:
    """Pub/sub message bus for real-time UI updates."""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._subscribers: Dict[str, List[Callable]] = {}
                    cls._instance._lock = threading.RLock()
        return cls._instance
    
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
    
    def publish(self, event_type: str, data: dict) -> None:
        """Publish an event to all subscribers."""
        with self._lock:
            callbacks = self._subscribers.get(event_type, []).copy()
        for callback in callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Message bus callback error: {e}")
    
    def publish_threadsafe(self, data: dict) -> None:
        """Thread-safe publish for background threads."""
        # For cross-thread communication, this would integrate with asyncio
        logger.debug(f"Threadsafe publish: {data}")
