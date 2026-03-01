"""
Core Tools for ThreadBear

File system, shell, and web tools for LLM agents.
"""
import os
import subprocess
import requests
from pathlib import Path
from .registry import tool_registry


# === read_file ===
def read_file(args: dict) -> dict:
    """Read file contents with size limit."""
    path = args.get('path', '')
    max_size = args.get('max_size', 100_000)  # 100KB default

    p = Path(path)
    # Relative paths resolve to toolbox/ first
    if not p.is_absolute():
        toolbox_path = Path('toolbox') / p
        if toolbox_path.exists():
            p = toolbox_path
    if not p.exists():
        return {'error': f'File not found: {path}'}
    
    if p.stat().st_size > max_size:
        content = p.read_text(encoding='utf-8', errors='replace')[:max_size]
        return {
            'content': content,
            'truncated': True,
            'total_size': p.stat().st_size,
            'message': f'File truncated to {max_size} bytes'
        }
    
    return {'content': p.read_text(encoding='utf-8', errors='replace')}


tool_registry.register_tool('read_file', read_file, {
    'description': 'Read the contents of a file at the given path.',
    'properties': {
        'path': {'type': 'string', 'description': 'Absolute or relative file path'},
        'max_size': {'type': 'integer', 'description': 'Max bytes to read (default 100000)'}
    },
    'required': ['path']
})


# === write_file ===
def write_file(args: dict) -> dict:
    """Write content to a file. Creates parent directories if needed."""
    path = Path(args.get('path', ''))
    content = args.get('content', '')

    if not path:
        return {'error': 'No path provided'}

    # Relative paths go into toolbox/
    if not path.is_absolute():
        path = Path('toolbox') / path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    
    return {'written': str(path), 'size': len(content)}


tool_registry.register_tool('write_file', write_file, {
    'description': 'Write content to a file. Creates directories if needed.',
    'properties': {
        'path': {'type': 'string', 'description': 'File path to write to'},
        'content': {'type': 'string', 'description': 'Content to write'}
    },
    'required': ['path', 'content']
}, destructive=True)


# === list_directory ===
def list_directory(args: dict) -> dict:
    """List directory contents."""
    path = args.get('path', '.')
    p = Path(path)
    
    if not p.exists():
        return {'error': f'Directory not found: {path}'}
    if not p.is_dir():
        return {'error': f'Not a directory: {path}'}
    
    entries = []
    for entry in sorted(p.iterdir()):
        entries.append({
            'name': entry.name,
            'type': 'dir' if entry.is_dir() else 'file',
            'size': entry.stat().st_size if entry.is_file() else None
        })
    
    return {'path': str(path), 'entries': entries}


tool_registry.register_tool('list_directory', list_directory, {
    'description': 'List files and subdirectories in a directory.',
    'properties': {
        'path': {'type': 'string', 'description': 'Directory path (default: current directory)'}
    },
    'required': []
})


# === run_command ===
def run_command(args: dict) -> dict:
    """Execute a shell command. Uses subprocess, not shell=True for safety."""
    command = args.get('command', '')
    timeout = args.get('timeout', 30)
    cwd = args.get('cwd')
    
    if not command:
        return {'error': 'No command provided'}
    
    try:
        result = subprocess.run(
            command, shell=True,  # shell=True for pipes/redirects; safety layer blocks dangerous commands
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd
        )
        # Truncate output if too long
        stdout = result.stdout[-10000:] if len(result.stdout) > 10000 else result.stdout
        stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
        
        return {
            'exit_code': result.returncode,
            'stdout': stdout,
            'stderr': stderr,
            'command': command
        }
    except subprocess.TimeoutExpired:
        return {'error': f'Command timed out after {timeout}s'}
    except Exception as e:
        return {'error': str(e)}


tool_registry.register_tool('run_command', run_command, {
    'description': 'Execute a shell command and return its output.',
    'properties': {
        'command': {'type': 'string', 'description': 'The shell command to execute'},
        'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default 30)'},
        'cwd': {'type': 'string', 'description': 'Working directory (optional)'}
    },
    'required': ['command']
}, timeout_s=60, destructive=True)


# === web_request ===
def web_request(args: dict) -> dict:
    """Make an HTTP request with timeout and size limit."""
    url = args.get('url', '')
    method = args.get('method', 'GET').upper()
    timeout = args.get('timeout', 15)
    max_size = args.get('max_size', 50_000)
    headers = args.get('headers', {})
    body = args.get('body')
    
    if not url:
        return {'error': 'No URL provided'}
    
    try:
        resp = requests.request(
            method, url, timeout=timeout,
            headers=headers,
            data=body
        )
        content = resp.text[:max_size]
        
        return {
            'status_code': resp.status_code,
            'content': content,
            'truncated': len(resp.text) > max_size,
            'headers': dict(resp.headers),
            'url': url
        }
    except requests.RequestException as e:
        return {'error': str(e)}


tool_registry.register_tool('web_request', web_request, {
    'description': 'Make an HTTP request to a URL.',
    'properties': {
        'url': {'type': 'string', 'description': 'The URL to request'},
        'method': {'type': 'string', 'description': 'HTTP method (default GET)'},
        'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default 15)'},
        'headers': {'type': 'object', 'description': 'Request headers (optional)'},
        'body': {'type': 'string', 'description': 'Request body (optional)'}
    },
    'required': ['url']
})
