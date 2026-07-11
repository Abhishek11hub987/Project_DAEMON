"""
C Integration Skill

Compile and execute C programs from c_modules directory.
"""

import logging
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class CIntegrationSkill:
    """Handle C program compilation and execution."""
    
    @staticmethod
    def _get_c_modules_dir() -> Path:
        """Get c_modules directory."""
        root = Path(__file__).parent.parent
        return root / "c_modules"
    
    @staticmethod
    def _get_compiled_dir() -> Path:
        """Get compiled binaries directory."""
        compiled = CIntegrationSkill._get_c_modules_dir() / "compiled"
        compiled.mkdir(parents=True, exist_ok=True)
        return compiled
    
    @staticmethod
    def find_c_files() -> list:
        """
        Find all C files in c_modules directory.
        
        Returns:
            List of C file paths
        """
        try:
            c_dir = CIntegrationSkill._get_c_modules_dir()
            if not c_dir.exists():
                return []
            
            c_files = list(c_dir.glob("**/*.c"))
            logger.info(f"Found {len(c_files)} C files")
            return c_files
        
        except Exception as e:
            logger.error(f"Error finding C files: {str(e)}")
            return []
    
    @staticmethod
    def _get_compiler() -> str:
        """
        Get available C compiler.
        
        Returns:
            Compiler command (gcc or clang)
        """
        if shutil.which("gcc"):
            return "gcc"
        elif shutil.which("clang"):
            return "clang"
        else:
            return None
    
    @staticmethod
    def compile_program(program_name: str, source_file: Optional[str] = None) -> Optional[Path]:
        """
        Compile a C program.
        
        Args:
            program_name: Name of the program (without .c)
            source_file: Optional explicit source file path
            
        Returns:
            Path to compiled binary or None
        """
        try:
            compiler = CIntegrationSkill._get_compiler()
            if not compiler:
                logger.error("No C compiler found (gcc or clang)")
                return None
            
            # Find source file
            if source_file:
                src_path = Path(source_file)
            else:
                # Search for program in c_modules
                c_dir = CIntegrationSkill._get_c_modules_dir()
                src_path = c_dir / f"{program_name}.c"
                
                # Try in subdirectories
                if not src_path.exists():
                    matches = list(c_dir.glob(f"**/{program_name}.c"))
                    if matches:
                        src_path = matches[0]
            
            if not src_path.exists():
                logger.error(f"Source file not found: {src_path}")
                return None
            
            # Output path
            compiled_dir = CIntegrationSkill._get_compiled_dir()
            out_path = compiled_dir / program_name
            if os.name == 'nt':  # Windows
                out_path = out_path.with_suffix('.exe')
            
            # Compile
            cmd = [compiler, "-o", str(out_path), str(src_path)]
            logger.info(f"Compiling: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(f"Compilation failed:\n{result.stderr}")
                return None
            
            logger.info(f"Successfully compiled: {out_path}")
            return out_path
        
        except subprocess.TimeoutExpired:
            logger.error("Compilation timeout")
            return None
        except Exception as e:
            logger.error(f"Compilation error: {str(e)}")
            return None
    
    @staticmethod
    def run_program(program_name: str, args: Optional[list] = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """
        Run a compiled C program.
        
        Args:
            program_name: Name of compiled program
            args: Command line arguments
            timeout: Execution timeout in seconds
            
        Returns:
            Dictionary with output and exit code or None
        """
        try:
            compiled_dir = CIntegrationSkill._get_compiled_dir()
            exe_path = compiled_dir / program_name
            
            if os.name == 'nt':  # Windows
                exe_path = exe_path.with_suffix('.exe')
            
            if not exe_path.exists():
                logger.warning(f"Compiled program not found: {exe_path}")
                # Try compiling it
                exe_path = CIntegrationSkill.compile_program(program_name)
                if not exe_path:
                    return None
            
            # Run program
            cmd = [str(exe_path)] + (args or [])
            logger.info(f"Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            
            return {
                "program": program_name,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0
            }
        
        except subprocess.TimeoutExpired:
            logger.error(f"Program timeout: {program_name}")
            return {"error": "Program timeout"}
        except Exception as e:
            logger.error(f"Execution error: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def compile_and_run(program_name: str, args: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """
        Compile and run a C program in one step.
        
        Args:
            program_name: Name of the program (without .c)
            args: Command line arguments
            
        Returns:
            Dictionary with output or None
        """
        try:
            # Compile
            exe_path = CIntegrationSkill.compile_program(program_name)
            if not exe_path:
                return {"error": "Compilation failed"}
            
            # Run
            return CIntegrationSkill.run_program(program_name, args)
        
        except Exception as e:
            logger.error(f"Compile and run error: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def list_programs() -> Dict[str, Any]:
        """
        List available C programs.
        
        Returns:
            Dictionary with source and compiled programs
        """
        try:
            c_files = CIntegrationSkill.find_c_files()
            compiled_dir = CIntegrationSkill._get_compiled_dir()
            
            source_programs = [f.stem for f in c_files]
            
            compiled_programs = []
            if compiled_dir.exists():
                for exe in compiled_dir.iterdir():
                    if exe.is_file() and (exe.suffix == '' or exe.suffix == '.exe'):
                        compiled_programs.append(exe.stem)
            
            return {
                "source_programs": source_programs,
                "compiled_programs": compiled_programs,
                "total_source": len(source_programs),
                "total_compiled": len(compiled_programs)
            }
        
        except Exception as e:
            logger.error(f"List programs error: {str(e)}")
            return {"error": str(e)}
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle C integration query.
        
        Args:
            query: User query
            
        Returns:
            Response string
        """
        import re
        
        query_lower = query.lower()
        
        # List programs
        if any(x in query_lower for x in ["list", "show", "available"]):
            programs = CIntegrationSkill.list_programs()
            
            response = "C Programs:\n"
            response += f"Source files: {programs['total_source']}\n"
            for prog in programs.get('source_programs', [])[:5]:
                response += f"  - {prog}.c\n"
            
            response += f"\nCompiled programs: {programs['total_compiled']}\n"
            for prog in programs.get('compiled_programs', [])[:5]:
                response += f"  - {prog}\n"
            
            return response
        
        # Run program
        match = re.search(r'(?:run|execute)\s+(\w+)(?:\s+with\s+(.+))?', query_lower)
        if match:
            program_name = match.group(1).strip()
            args_str = match.group(2).strip() if match.group(2) else None
            args = args_str.split() if args_str else None
            
            result = CIntegrationSkill.compile_and_run(program_name, args)
            
            if result is None:
                return f"Failed to run program: {program_name}"
            
            if "error" in result:
                return f"Error: {result['error']}"
            
            response = f"Program: {result['program']}\n"
            response += f"Exit Code: {result['exit_code']}\n"
            
            if result['stdout']:
                response += f"\nOutput:\n{result['stdout']}"
            
            if result['stderr']:
                response += f"\nErrors:\n{result['stderr']}"
            
            return response
        
        # Compile program
        match = re.search(r'(?:compile|build)\s+(\w+)', query_lower)
        if match:
            program_name = match.group(1).strip()
            exe_path = CIntegrationSkill.compile_program(program_name)
            
            if exe_path:
                return f"Successfully compiled: {exe_path.name}"
            else:
                return f"Failed to compile: {program_name}"
        
        # Default help
        return ("C Integration commands:\n"
               "  - list available programs\n"
               "  - compile program_name\n"
               "  - run program_name\n"
               "  - execute program_name with arg1 arg2")
