"""
Tool Registry for ThreadBear

Central registry for all tools available to LLM agents.
Tools are registered with schemas (OpenAI function-calling format)
and executed with timeout + safety validation.
"""
import threading
import json
from typing import Dict, List, Any, Optional, Callable


class ToolRegistry:
    """
    Central registry for all tools available to LLM agents.
    """

    def __init__(self):
        self.tools: Dict[str, Dict] = {}

    def register_tool(self, name: str, fn: Callable, schema: Dict,
                      timeout_s: int = 30, destructive: bool = False):
        """
        Register a tool with its function, schema, and safety metadata.

        Args:
            name: Tool name (must match schema function name)
            fn: Callable that takes (args: dict) -> dict
            schema: OpenAI function parameter schema (JSON Schema object)
            timeout_s: Execution timeout in seconds
            destructive: If True, may require user confirmation
        """
        self.tools[name] = {
            'fn': fn,
            'schema': schema,
            'timeout_s': timeout_s,
            'destructive': destructive
        }

    def get_schemas_for_provider(self, allowed_tools: Optional[List[str]] = None) -> List[Dict]:
        """
        Get OpenAI-compatible tool schemas, optionally filtered by allowed list.
        Returns list suitable for passing as `tools` parameter to LLM APIs.
        """
        schemas = []
        for name, tool in self.tools.items():
            if allowed_tools and name not in allowed_tools:
                continue
            schemas.append({
                'type': 'function',
                'function': {
                    'name': name,
                    'description': tool['schema'].get('description', name),
                    'parameters': {
                        'type': 'object',
                        'properties': tool['schema'].get('properties', {}),
                        'required': tool['schema'].get('required', [])
                    }
                }
            })
        return schemas

    def execute_tool(self, name: str, args: Dict,
                     safety_manager=None) -> Dict[str, Any]:
        """
        Execute a tool with timeout and safety checks.
        Uses threading.Timer for Windows compatibility (no signal.alarm).
        Returns: {'success': bool, 'result': ..., 'error': ...}
        """
        if name not in self.tools:
            return {'success': False, 'error': f'Unknown tool: {name}'}

        tool = self.tools[name]

        # Safety validation
        if safety_manager:
            error = safety_manager.validate_tool_call(name, args)
            if error:
                return {'success': False, 'error': f'Safety blocked: {error}'}

        # Execute with timeout (threading for Windows compat)
        result_container = [None]
        exception_container = [None]

        def run():
            try:
                result_container[0] = tool['fn'](args)
            except Exception as e:
                exception_container[0] = e

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=tool['timeout_s'])

        if thread.is_alive():
            return {'success': False, 'error': f'Tool {name} timed out after {tool["timeout_s"]}s'}
        if exception_container[0]:
            return {'success': False, 'error': str(exception_container[0])}

        return {'success': True, 'result': result_container[0], 'tool': name}

    def list_tools(self) -> Dict[str, Dict]:
        """List all registered tools with metadata."""
        return {name: {
            'description': tool['schema'].get('description', ''),
            'destructive': tool['destructive'],
            'timeout_s': tool['timeout_s']
        } for name, tool in self.tools.items()}


# Singleton instance
tool_registry = ToolRegistry()
