"""
File Management Skill

Handle file operations, directory navigation, and file search.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class FileSkill:
    """Handle file management operations."""
    
    @staticmethod
    def _get_project_root() -> Path:
        """Get project root directory."""
        return Path(__file__).parent.parent
    
    @staticmethod
    def _safe_path(file_path: str) -> Optional[Path]:
        """
        Safely resolve file path (prevent directory traversal).
        
        Args:
            file_path: User-provided path
            
        Returns:
            Resolved Path or None if invalid
        """
        try:
            root = FileSkill._get_project_root()
            
            # Handle relative paths
            if file_path.startswith('~/'):
                path = Path.home() / file_path[2:]
            else:
                path = root / file_path
            
            # Resolve the path
            resolved = path.resolve()
            
            # Check if resolved path is within project root (unless it's a home directory request)
            if file_path.startswith('~/'):
                return resolved
            
            if not str(resolved).startswith(str(root)):
                logger.warning(f"Path traversal attempt: {file_path}")
                return None
            
            return resolved
        
        except Exception as e:
            logger.error(f"Path resolution error: {str(e)}")
            return None
    
    @staticmethod
    def list_files(directory: str = ".", recursive: bool = False) -> Optional[Dict[str, Any]]:
        """
        List files in a directory.
        
        Args:
            directory: Directory path (default: current)
            recursive: Whether to list recursively
            
        Returns:
            Dictionary with file information or None
        """
        try:
            if directory == ".":
                dir_path = FileSkill._get_project_root()
            else:
                dir_path = FileSkill._safe_path(directory)
            
            if not dir_path or not dir_path.is_dir():
                return None
            
            files = []
            dirs = []
            
            if recursive:
                for item in dir_path.rglob('*'):
                    if item.is_file():
                        files.append({
                            "name": item.name,
                            "path": str(item.relative_to(dir_path)),
                            "size": item.stat().st_size
                        })
                    elif item.is_dir():
                        dirs.append({
                            "name": item.name,
                            "path": str(item.relative_to(dir_path))
                        })
            else:
                for item in sorted(dir_path.iterdir()):
                    if item.is_file():
                        files.append({
                            "name": item.name,
                            "size": item.stat().st_size
                        })
                    elif item.is_dir():
                        dirs.append({
                            "name": item.name
                        })
            
            return {
                "directory": str(dir_path),
                "files": files[:20],  # Limit to 20
                "directories": dirs[:20],
                "file_count": len(files),
                "dir_count": len(dirs)
            }
        
        except Exception as e:
            logger.error(f"Directory listing failed: {str(e)}")
            return None
    
    @staticmethod
    def search_files(pattern: str, directory: str = ".") -> Optional[List[str]]:
        """
        Search for files matching a pattern.
        
        Args:
            pattern: File pattern (e.g., "*.py")
            directory: Directory to search in
            
        Returns:
            List of matching files or None
        """
        try:
            if directory == ".":
                dir_path = FileSkill._get_project_root()
            else:
                dir_path = FileSkill._safe_path(directory)
            
            if not dir_path or not dir_path.is_dir():
                return None
            
            matches = []
            for file_path in dir_path.rglob(pattern):
                if file_path.is_file():
                    matches.append(str(file_path.relative_to(dir_path)))
            
            logger.info(f"Found {len(matches)} files matching {pattern}")
            return matches[:20]  # Limit to 20
        
        except Exception as e:
            logger.error(f"File search failed: {str(e)}")
            return None
    
    @staticmethod
    def get_file_info(file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Dictionary with file info or None
        """
        try:
            safe_path = FileSkill._safe_path(file_path)
            if not safe_path or not safe_path.is_file():
                return None
            
            stat = safe_path.stat()
            
            return {
                "name": safe_path.name,
                "path": str(safe_path),
                "size_bytes": stat.st_size,
                "size_kb": round(stat.st_size / 1024, 2),
                "created": stat.st_ctime,
                "modified": stat.st_mtime,
                "is_readable": os.access(safe_path, os.R_OK),
                "is_writable": os.access(safe_path, os.W_OK),
                "suffix": safe_path.suffix
            }
        
        except Exception as e:
            logger.error(f"File info failed: {str(e)}")
            return None
    
    @staticmethod
    def copy_file(source: str, destination: str) -> bool:
        """
        Copy a file.
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Returns:
            True if successful
        """
        try:
            src = FileSkill._safe_path(source)
            dst = FileSkill._safe_path(destination)
            
            if not src or not src.is_file() or not dst:
                return False
            
            shutil.copy2(src, dst)
            logger.info(f"Copied {src} to {dst}")
            return True
        
        except Exception as e:
            logger.error(f"Copy failed: {str(e)}")
            return False
    
    @staticmethod
    def delete_file(file_path: str) -> bool:
        """
        Delete a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            True if successful
        """
        try:
            safe_path = FileSkill._safe_path(file_path)
            if not safe_path or not safe_path.is_file():
                return False
            
            safe_path.unlink()
            logger.info(f"Deleted {safe_path}")
            return True
        
        except Exception as e:
            logger.error(f"Delete failed: {str(e)}")
            return False
    
    @staticmethod
    def rename_file(old_path: str, new_name: str) -> bool:
        """
        Rename a file.
        
        Args:
            old_path: Current file path
            new_name: New file name
            
        Returns:
            True if successful
        """
        try:
            safe_path = FileSkill._safe_path(old_path)
            if not safe_path or not safe_path.is_file():
                return False
            
            new_path = safe_path.parent / new_name
            safe_path.rename(new_path)
            logger.info(f"Renamed {safe_path} to {new_path}")
            return True
        
        except Exception as e:
            logger.error(f"Rename failed: {str(e)}")
            return False
    
    @staticmethod
    def get_directory_tree(directory: str = ".", max_depth: int = 2) -> Optional[str]:
        """
        Get a tree view of directory structure.
        
        Args:
            directory: Directory path
            max_depth: Maximum directory depth
            
        Returns:
            Tree string or None
        """
        try:
            if directory == ".":
                dir_path = FileSkill._get_project_root()
            else:
                dir_path = FileSkill._safe_path(directory)
            
            if not dir_path or not dir_path.is_dir():
                return None
            
            def get_tree(path: Path, prefix: str = "", depth: int = 0) -> str:
                if depth >= max_depth:
                    return ""
                
                tree = ""
                items = sorted(path.iterdir())
                
                for i, item in enumerate(items):
                    is_last = i == len(items) - 1
                    current_prefix = "└── " if is_last else "├── "
                    tree += prefix + current_prefix + item.name + "\n"
                    
                    if item.is_dir() and depth < max_depth - 1:
                        next_prefix = prefix + ("    " if is_last else "│   ")
                        tree += get_tree(item, next_prefix, depth + 1)
                
                return tree
            
            tree = dir_path.name + "/\n"
            tree += get_tree(dir_path)
            return tree
        
        except Exception as e:
            logger.error(f"Tree generation failed: {str(e)}")
            return None
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle file management query.
        
        Args:
            query: User query
            
        Returns:
            Response string
        """
        import re
        
        query_lower = query.lower()
        
        # List files
        if any(x in query_lower for x in ["list", "show", "display", "ls"]):
            # Extract directory if specified
            match = re.search(r'(?:list|show|display|ls)\s+(?:files\s+)?(?:in|from)?\s*(.+)?', query_lower)
            directory = match.group(1).strip() if match and match.group(1) else "."
            
            files_info = FileSkill.list_files(directory)
            if not files_info:
                return f"Could not list directory: {directory}"
            
            response = f"Directory: {files_info['directory']}\n"
            response += f"Files ({files_info['file_count']}):\n"
            for f in files_info['files'][:5]:
                size_kb = f['size'] / 1024 if 'size' in f else 0
                response += f"  {f['name']} ({size_kb:.1f} KB)\n"
            
            if files_info['directories']:
                response += f"\nDirectories:\n"
                for d in files_info['directories'][:5]:
                    response += f"  {d['name']}/\n"
            
            return response
        
        # Search files
        if any(x in query_lower for x in ["search", "find"]):
            match = re.search(r'(?:search|find)\s+(?:for)?\s+(.+?)(?:\s+in\s+(.+))?', query_lower)
            if not match:
                return "Usage: search for *.py or find *.txt in directory"
            
            pattern = match.group(1).strip()
            directory = match.group(2).strip() if match.group(2) else "."
            
            matches = FileSkill.search_files(pattern, directory)
            if matches is None:
                return f"Could not search in {directory}"
            
            if not matches:
                return f"No files matching pattern: {pattern}"
            
            response = f"Found {len(matches)} files matching '{pattern}':\n"
            for f in matches[:10]:
                response += f"  {f}\n"
            
            return response
        
        # Get file info
        match = re.search(r'(?:info|details|about)\s+(.+)', query_lower)
        if match:
            file_path = match.group(1).strip()
            info = FileSkill.get_file_info(file_path)
            if not info:
                return f"Could not get info for: {file_path}"
            
            response = f"File: {info['name']}\n"
            response += f"Size: {info['size_kb']} KB\n"
            response += f"Readable: {info['is_readable']}\n"
            response += f"Writable: {info['is_writable']}\n"
            
            return response
        
        # Show directory tree
        if "tree" in query_lower:
            tree = FileSkill.get_directory_tree(".", max_depth=3)
            if tree:
                return tree
            return "Could not generate directory tree"
        
        # Default help
        return ("File management commands:\n"
               "  - list files [in directory]\n"
               "  - search for *.py\n"
               "  - info about filename\n"
               "  - show directory tree")
