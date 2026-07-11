"""
Calculator Skill - Handle mathematical calculations
"""

import logging
import re
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class CalculatorSkill:
    """Handle mathematical calculations."""
    
    OPERATORS = {
        '+': lambda a, b: a + b,
        '-': lambda a, b: a - b,
        '*': lambda a, b: a * b,
        '/': lambda a, b: a / b if b != 0 else None,
        'x': lambda a, b: a * b,
        '÷': lambda a, b: a / b if b != 0 else None,
    }
    
    @staticmethod
    def parse_expression(text: str) -> Optional[Tuple[float, str, float]]:
        """
        Parse mathematical expression from text.
        
        Args:
            text: User input
            
        Returns:
            (number1, operator, number2) or None if can't parse
        """
        # Pattern: number operator number
        pattern = r'(\d+\.?\d*)\s*([\+\-\*\/x÷])\s*(\d+\.?\d*)'
        match = re.search(pattern, text.lower())
        
        if match:
            num1 = float(match.group(1))
            op = match.group(2)
            num2 = float(match.group(3))
            return (num1, op, num2)
        
        return None
    
    @staticmethod
    def calculate(num1: float, operator: str, num2: float) -> Optional[float]:
        """
        Perform calculation.
        
        Args:
            num1: First number
            operator: Operator (+, -, *, /, x, ÷)
            num2: Second number
            
        Returns:
            Result or None if invalid
        """
        if operator not in CalculatorSkill.OPERATORS:
            return None
        
        handler = CalculatorSkill.OPERATORS[operator]
        result = handler(num1, num2)
        
        return result
    
    @staticmethod
    def format_result(num1: float, op: str, num2: float, result: Optional[float]) -> str:
        """Format calculation result for display."""
        if result is None:
            if op in ['/', '÷'] and num2 == 0:
                return f"Cannot divide by zero."
            return "Calculation failed."
        
        # Format result nicely
        if result == int(result):
            result_str = str(int(result))
        else:
            result_str = f"{result:.4f}".rstrip('0').rstrip('.')
        
        # Display operator
        display_op = '*' if op == 'x' else op
        display_op = '/' if op == '÷' else display_op
        
        return f"{num1} {display_op} {num2} = {result_str}"
    
    @staticmethod
    def handle(query: str) -> str:
        """
        Handle calculator query.
        
        Args:
            query: User query
            
        Returns:
            Calculation result as string
        """
        parsed = CalculatorSkill.parse_expression(query)
        
        if not parsed:
            return "I can calculate simple expressions like '2 + 2' or '10 * 5'. Please provide a clear math problem."
        
        num1, op, num2 = parsed
        result = CalculatorSkill.calculate(num1, op, num2)
        
        return CalculatorSkill.format_result(num1, op, num2, result)
