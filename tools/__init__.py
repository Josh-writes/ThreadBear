"""
ThreadBear Tool System

Tool registry, core tools, and safety management for LLM function calling.
"""
from .registry import tool_registry, ToolRegistry
from .safety import ToolSafetyManager

__all__ = ['tool_registry', 'ToolRegistry', 'ToolSafetyManager']

# Import core tools to register them
from . import core_tools

# Import agent tools (todo/plan management)
from . import agent_tools
