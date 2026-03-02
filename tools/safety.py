"""
Tool Safety Manager for ThreadBear

Validates tool calls before execution.
Adapted from lmagent Safety class (agent_core.py:783-895) and
tool_calling_implementation_roadmap.md safety manager.
"""
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List


class ToolSafetyManager:
    """
    Validates tool calls before execution.
    """

    BLOCKED_COMMANDS = [
        'rm -rf /', 'rm -rf ~', 'rm -rf *',
        'del /f /s /q', 'format ', 'shutdown', 'reboot',
        'mkfs', 'dd if=', ':(){', ':(){ :|:& };:',  # fork bomb
        'reg delete', 'net stop',
    ]

    SENSITIVE_PATHS = [
        '.env', '.git/config', 'config.json',    # ThreadBear sensitive
        '.ssh/', 'id_rsa', 'id_ed25519',          # SSH keys
        '.pem', '.key',                             # Certificates
        '/etc/shadow', '/etc/passwd', '/root/',     # System
        'System32', 'system32',                     # Windows system
    ]

    SHELL_INJECTION_PATTERNS = [
        r';\s*rm\b', r'&&\s*rm\b', r'\|\|\s*rm\b',
        r'`[^`]*`',       # Backtick injection
        r'\$\([^)]*\)',    # Command substitution
    ]

    def __init__(self, config: Optional[Dict] = None):
        config = config or {}
        self.extra_blocked = config.get('blocked_commands', [])
        self.workspace_root = config.get('tool_workspace', None)

    def validate_tool_call(self, tool_name: str, args: Dict) -> Optional[str]:
        """Returns error string if blocked, None if allowed."""
        if tool_name in ('read_file', 'write_file'):
            return self._validate_path(args.get('path', ''))
        if tool_name == 'run_command':
            return self._validate_command(args.get('command', ''))
        if tool_name in ('web_request', 'web_search'):
            return self._validate_url(args.get('url', ''))
        return None

    def _validate_path(self, path: str) -> Optional[str]:
        """Check for path traversal and sensitive files."""
        if not path:
            return 'Empty path'
        
        # Normalize
        norm = str(Path(path)).replace('\\', '/')

        # Path traversal
        if '..' in norm:
            return f'Path traversal detected: {path}'

        # Sensitive paths
        for sp in self.SENSITIVE_PATHS:
            if sp in norm:
                return f'Access to sensitive path blocked: {sp}'

        # Workspace restriction
        if self.workspace_root:
            try:
                resolved = Path(path).resolve()
                workspace = Path(self.workspace_root).resolve()
                if not str(resolved).startswith(str(workspace)):
                    return f'Path outside workspace: {path}'
            except (OSError, ValueError):
                return f'Invalid path: {path}'

        return None

    def _validate_command(self, command: str) -> Optional[str]:
        """Check against blocked commands and injection patterns."""
        if not command:
            return 'Empty command'
        
        cmd_lower = command.lower().strip()

        # Blocked commands
        for blocked in self.BLOCKED_COMMANDS + self.extra_blocked:
            if blocked.lower() in cmd_lower:
                return f'Blocked command: {blocked}'

        # Shell injection patterns
        for pattern in self.SHELL_INJECTION_PATTERNS:
            if re.search(pattern, command):
                return 'Potential shell injection detected'

        return None

    def _validate_url(self, url: str) -> Optional[str]:
        """Block internal/localhost URLs and file:// protocol."""
        if not url:
            return 'Empty URL'
        
        try:
            parsed = urlparse(url)
        except Exception:
            return f'Invalid URL: {url}'

        # Block file:// protocol
        if parsed.scheme == 'file':
            return 'file:// protocol not allowed'

        # Block localhost/internal
        host = parsed.hostname or ''
        if host in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
            return 'Localhost URLs not allowed'
        if host.startswith('10.') or host.startswith('192.168.') or host.startswith('172.'):
            return 'Internal network URLs not allowed'

        return None
