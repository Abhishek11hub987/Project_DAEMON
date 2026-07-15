"""Cross-Platform OS Utilities"""
import platform
import subprocess
from typing import Tuple
import logging

logger = logging.getLogger(__name__)

class OSDetector:
    """Detects and manages operating system information."""
    
    @staticmethod
    def get_os_type() -> str:
        """Get the operating system type."""
        system = platform.system().lower()
        if system == "windows":
            return "windows"
        elif system == "linux":
            return "linux"
        elif system == "darwin":
            return "darwin"
        return "unknown"
    
    @staticmethod
    def is_windows() -> bool:
        return OSDetector.get_os_type() == "windows"
    
    @staticmethod
    def is_linux() -> bool:
        return OSDetector.get_os_type() == "linux"

class CommandExecutor:
    """Execute system commands."""
    
    @staticmethod
    def execute_command(command: str, shell: bool = True, timeout: float = 30.0) -> Tuple[int, str, str]:
        """Execute a system command."""
        try:
            result = subprocess.run(
                command,
                shell=shell,  # nosec B602
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Command timed out: {command}")
        except Exception as e:
            raise RuntimeError(f"Command failed: {str(e)}")

def get_platform_info() -> str:
    """Get platform information."""
    return platform.platform()
