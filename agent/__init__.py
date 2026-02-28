"""
ThreadBear Agent Engine

Autonomous agent execution for work-order branches.
"""
from .loop_detector import LoopDetector
from .completion_detector import detect_completion
from .todo_manager import TodoManager
from .plan_manager import PlanManager
from .execution_engine import AgentExecutionEngine

__all__ = [
    'LoopDetector',
    'detect_completion',
    'TodoManager',
    'PlanManager',
    'AgentExecutionEngine',
]
