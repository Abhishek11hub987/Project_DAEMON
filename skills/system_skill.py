"""
System Monitoring Skill

Monitor CPU, memory, processes, and system information.
"""

import logging
import os
import platform
import subprocess
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class SystemSkill:
    """Monitor and report system information."""
    
    @staticmethod
    def get_cpu_info() -> Dict[str, Any]:
        """
        Get CPU information.
        
        Returns:
            Dictionary with CPU stats
        """
        try:
            import psutil
            
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count(logical=False)
            cpu_count_logical = psutil.cpu_count(logical=True)
            
            return {
                "usage_percent": cpu_percent,
                "count_physical": cpu_count,
                "count_logical": cpu_count_logical,
                "status": "good" if cpu_percent < 50 else "warning" if cpu_percent < 80 else "high"
            }
        except ImportError:
            logger.warning("psutil not installed")
            return {"error": "psutil not installed"}
        except Exception as e:
            logger.error(f"CPU info failed: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def get_memory_info() -> Dict[str, Any]:
        """
        Get memory usage information.
        
        Returns:
            Dictionary with memory stats
        """
        try:
            import psutil
            
            memory = psutil.virtual_memory()
            
            return {
                "total_gb": round(memory.total / (1024**3), 2),
                "used_gb": round(memory.used / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "percent": memory.percent,
                "status": "good" if memory.percent < 50 else "warning" if memory.percent < 80 else "critical"
            }
        except ImportError:
            logger.warning("psutil not installed")
            return {"error": "psutil not installed"}
        except Exception as e:
            logger.error(f"Memory info failed: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def get_disk_info(path: str = "/") -> Dict[str, Any]:
        """
        Get disk usage information.
        
        Args:
            path: Path to check (default: root)
            
        Returns:
            Dictionary with disk stats
        """
        try:
            import psutil
            
            disk = psutil.disk_usage(path)
            
            return {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent": disk.percent,
                "status": "good" if disk.percent < 70 else "warning" if disk.percent < 90 else "critical"
            }
        except ImportError:
            logger.warning("psutil not installed")
            return {"error": "psutil not installed"}
        except Exception as e:
            logger.error(f"Disk info failed: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def list_processes(limit: int = 10) -> List[Dict[str, Any]]:
        """
        List top processes by CPU usage.
        
        Args:
            limit: Number of processes to show
            
        Returns:
            List of process info dictionaries
        """
        try:
            import psutil
            
            processes = []
            
            for proc in sorted(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
                             key=lambda p: p.info['cpu_percent'],
                             reverse=True)[:limit]:
                try:
                    processes.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'],
                        "cpu_percent": round(proc.info['cpu_percent'], 1),
                        "memory_percent": round(proc.info['memory_percent'], 1)
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            return processes
        
        except ImportError:
            logger.warning("psutil not installed")
            return []
        except Exception as e:
            logger.error(f"Process listing failed: {str(e)}")
            return []
    
    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """
        Get general system information.
        
        Returns:
            Dictionary with system info
        """
        try:
            return {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "architecture": platform.machine(),
                "hostname": platform.node(),
                "processor": platform.processor(),
                "python_version": platform.python_version()
            }
        except Exception as e:
            logger.error(f"System info failed: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def get_network_info() -> Dict[str, Any]:
        """
        Get network information.
        
        Returns:
            Dictionary with network stats
        """
        try:
            import psutil
            
            net_io = psutil.net_io_counters()
            
            return {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "errors_in": net_io.errin,
                "errors_out": net_io.errout
            }
        except ImportError:
            logger.warning("psutil not installed")
            return {"error": "psutil not installed"}
        except Exception as e:
            logger.error(f"Network info failed: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle system monitoring query.
        
        Args:
            query: User query
            
        Returns:
            Response string
        """
        query_lower = query.lower()
        
        # CPU query
        if any(x in query_lower for x in ["cpu", "processor", "usage"]):
            cpu_info = SystemSkill.get_cpu_info()
            if "error" in cpu_info:
                return cpu_info["error"]
            
            return (f"CPU Usage: {cpu_info['usage_percent']}%\n"
                   f"Physical Cores: {cpu_info['count_physical']}\n"
                   f"Logical Cores: {cpu_info['count_logical']}\n"
                   f"Status: {cpu_info['status']}")
        
        # Memory query
        if any(x in query_lower for x in ["memory", "ram", "mem"]):
            mem_info = SystemSkill.get_memory_info()
            if "error" in mem_info:
                return mem_info["error"]
            
            return (f"Memory Usage: {mem_info['percent']}%\n"
                   f"Total: {mem_info['total_gb']} GB\n"
                   f"Used: {mem_info['used_gb']} GB\n"
                   f"Available: {mem_info['available_gb']} GB\n"
                   f"Status: {mem_info['status']}")
        
        # Disk query
        if any(x in query_lower for x in ["disk", "storage", "space"]):
            disk_info = SystemSkill.get_disk_info()
            if "error" in disk_info:
                return disk_info["error"]
            
            return (f"Disk Usage: {disk_info['percent']}%\n"
                   f"Total: {disk_info['total_gb']} GB\n"
                   f"Used: {disk_info['used_gb']} GB\n"
                   f"Free: {disk_info['free_gb']} GB\n"
                   f"Status: {disk_info['status']}")
        
        # Process query
        if any(x in query_lower for x in ["process", "running", "top"]):
            processes = SystemSkill.list_processes(5)
            if not processes:
                return "Could not retrieve process information."
            
            response = "Top 5 processes by CPU usage:\n"
            for proc in processes:
                response += f"  {proc['name']}: {proc['cpu_percent']}% CPU, {proc['memory_percent']}% RAM\n"
            return response
        
        # System info query
        if any(x in query_lower for x in ["system", "info", "os", "platform"]):
            sys_info = SystemSkill.get_system_info()
            
            return (f"OS: {sys_info['platform']} {sys_info['platform_release']}\n"
                   f"Architecture: {sys_info['architecture']}\n"
                   f"Hostname: {sys_info['hostname']}\n"
                   f"Python: {sys_info['python_version']}")
        
        # Default: show summary
        cpu = SystemSkill.get_cpu_info()
        mem = SystemSkill.get_memory_info()
        disk = SystemSkill.get_disk_info()
        
        response = "System Status:\n"
        
        if "error" not in cpu:
            response += f"CPU: {cpu['usage_percent']}%\n"
        if "error" not in mem:
            response += f"Memory: {mem['percent']}%\n"
        if "error" not in disk:
            response += f"Disk: {disk['percent']}%"
        
        return response
