"""
Script sandbox: capability scanning and restricted execution for toolbelt scripts.

Two components:
- ScriptScanner: AST + regex analysis to detect what capabilities a script uses
- SandboxedRunner: executes scripts with filtered environment variables and timeouts
"""

import ast
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Capability detection patterns
# ---------------------------------------------------------------------------

NETWORK_IMPORTS = {"requests", "urllib", "httpx", "aiohttp", "socket", "smtplib",
                   "http", "ftplib", "xmlrpc", "websocket", "websockets"}

SUBPROCESS_IMPORTS = {"subprocess"}

FILE_IO_IMPORTS = {"shutil", "pathlib", "glob", "tempfile"}

DANGEROUS_IMPORTS = {"ctypes"}

SUBPROCESS_CALLS = {
    ("os", "system"), ("os", "popen"), ("os", "exec"),
    ("subprocess", "run"), ("subprocess", "Popen"),
    ("subprocess", "call"), ("subprocess", "check_output"),
    ("subprocess", "check_call"),
}

FILE_IO_CALLS = {
    ("shutil", "rmtree"), ("shutil", "copy"), ("shutil", "copy2"),
    ("shutil", "move"), ("shutil", "copytree"),
    ("os", "remove"), ("os", "rename"), ("os", "unlink"),
    ("os", "rmdir"), ("os", "makedirs"), ("os", "mkdir"),
}

ENV_CALLS = {
    ("os", "getenv"),
}

DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}

# Regex fallbacks for things AST can miss
REGEX_PATTERNS = [
    (r'\b__import__\s*\(', "dangerous", "__import__() call"),
    (r'\bexec\s*\(', "dangerous", "exec() call"),
    (r'\beval\s*\(', "dangerous", "eval() call"),
    (r'\bos\.environ\b', "env_access", "os.environ access"),
]


class ScriptScanner:
    """Static analysis scanner for Python scripts."""

    def scan(self, script_path: str) -> dict:
        """Scan a script and return capability analysis.

        Returns:
            {
                "capabilities": ["network", "file_io", ...],
                "risk_level": "safe" | "warning" | "danger",
                "details": [{"capability": "...", "evidence": "..."}, ...],
                "env_vars": ["VAR_NAME", ...]  # detected env var names
            }
        """
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                source = f.read()
        except (OSError, IOError) as e:
            return {
                "capabilities": ["dangerous"],
                "risk_level": "danger",
                "details": [{"capability": "dangerous", "evidence": f"Could not read file: {e}"}],
                "env_vars": [],
            }

        details = []
        env_vars = []

        # AST analysis
        try:
            tree = ast.parse(source, filename=script_path)
            self._walk_ast(tree, details, env_vars)
        except SyntaxError as e:
            details.append({
                "capability": "dangerous",
                "evidence": f"Could not parse: {e}",
            })

        # Regex fallback
        self._regex_scan(source, details)

        # Deduplicate
        seen = set()
        unique_details = []
        for d in details:
            key = (d["capability"], d["evidence"])
            if key not in seen:
                seen.add(key)
                unique_details.append(d)

        capabilities = sorted(set(d["capability"] for d in unique_details))
        risk_level = self._risk_level(capabilities)

        return {
            "capabilities": capabilities,
            "risk_level": risk_level,
            "details": unique_details,
            "env_vars": sorted(set(env_vars)),
        }

    def _walk_ast(self, tree: ast.AST, details: list, env_vars: list):
        for node in ast.walk(tree):
            self._check_imports(node, details)
            self._check_calls(node, details, env_vars)
            self._check_open(node, details)
            self._check_environ_subscript(node, details, env_vars)

    def _check_imports(self, node: ast.AST, details: list):
        modules = set()
        lineno = getattr(node, "lineno", "?")

        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])

        for mod in modules:
            if mod in NETWORK_IMPORTS:
                details.append({"capability": "network",
                                "evidence": f"import {mod} (line {lineno})"})
            if mod in SUBPROCESS_IMPORTS:
                details.append({"capability": "subprocess",
                                "evidence": f"import {mod} (line {lineno})"})
            if mod in FILE_IO_IMPORTS:
                details.append({"capability": "file_io",
                                "evidence": f"import {mod} (line {lineno})"})
            if mod in DANGEROUS_IMPORTS:
                details.append({"capability": "dangerous",
                                "evidence": f"import {mod} (line {lineno})"})
            if mod == "os":
                # os is multi-purpose, don't flag just the import
                pass

    def _check_calls(self, node: ast.AST, details: list, env_vars: list):
        if not isinstance(node, ast.Call):
            return

        lineno = getattr(node, "lineno", "?")
        func = node.func

        # Simple name calls: eval(), exec(), compile(), open()
        if isinstance(func, ast.Name):
            if func.id in DANGEROUS_CALLS:
                details.append({"capability": "dangerous",
                                "evidence": f"{func.id}() call (line {lineno})"})

        # Attribute calls: os.system(), subprocess.run(), etc.
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)

                if pair in SUBPROCESS_CALLS:
                    details.append({"capability": "subprocess",
                                    "evidence": f"{func.value.id}.{func.attr}() (line {lineno})"})
                if pair in FILE_IO_CALLS:
                    details.append({"capability": "file_io",
                                    "evidence": f"{func.value.id}.{func.attr}() (line {lineno})"})
                if pair in ENV_CALLS:
                    details.append({"capability": "env_access",
                                    "evidence": f"{func.value.id}.{func.attr}() (line {lineno})"})
                    # Try to extract the env var name
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        env_vars.append(node.args[0].value)

                # os.environ.get()
                if func.attr == "get" and isinstance(func.value, ast.Attribute):
                    if (isinstance(func.value.value, ast.Name) and
                            func.value.value.id == "os" and func.value.attr == "environ"):
                        details.append({"capability": "env_access",
                                        "evidence": f"os.environ.get() (line {lineno})"})
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            env_vars.append(node.args[0].value)

    def _check_open(self, node: ast.AST, details: list):
        """Detect open() calls for file I/O."""
        if not isinstance(node, ast.Call):
            return
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            lineno = getattr(node, "lineno", "?")
            details.append({"capability": "file_io",
                            "evidence": f"open() call (line {lineno})"})

    def _check_environ_subscript(self, node: ast.AST, details: list, env_vars: list):
        """Detect os.environ['KEY'] or os.environ[\"KEY\"] access."""
        if not isinstance(node, ast.Subscript):
            return
        val = node.value
        if (isinstance(val, ast.Attribute) and val.attr == "environ" and
                isinstance(val.value, ast.Name) and val.value.id == "os"):
            lineno = getattr(node, "lineno", "?")
            details.append({"capability": "env_access",
                            "evidence": f"os.environ[] access (line {lineno})"})
            # Extract key if it's a string constant
            slc = node.slice
            if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                env_vars.append(slc.value)

    def _regex_scan(self, source: str, details: list):
        for pattern, capability, label in REGEX_PATTERNS:
            for match in re.finditer(pattern, source):
                line_num = source[:match.start()].count("\n") + 1
                details.append({"capability": capability,
                                "evidence": f"{label} (line {line_num})"})

    def _risk_level(self, capabilities: list) -> str:
        if "dangerous" in capabilities:
            return "danger"
        if any(c in capabilities for c in ("network", "file_io", "env_access", "subprocess")):
            return "warning"
        return "safe"


class SandboxedRunner:
    """Execute toolbelt scripts with restricted environment."""

    # Minimal system env vars needed for Python to work on Windows
    SYSTEM_VARS = [
        "PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "SYSTEMDRIVE", "WINDIR",
        # Python needs these
        "PYTHONPATH", "PYTHONHOME",
    ]

    def run(self, script_path: str, chat_path: str, permissions: dict,
            project_root: str = None) -> dict:
        """Run a script with sandboxed environment.

        Args:
            script_path: Full path to the script
            chat_path: Path to the chat JSON file (passed as argv[1])
            permissions: Dict with allow_env, timeout, etc.
            project_root: Working directory for the subprocess

        Returns:
            {"success": bool, "output": str, "error": str, "returncode": int}
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        timeout = permissions.get("timeout", 60)
        allow_env = permissions.get("allow_env", [])

        # Build filtered environment
        filtered_env = {}

        # Add minimal system vars from current env
        for var in self.SYSTEM_VARS:
            val = os.environ.get(var)
            if val is not None:
                filtered_env[var] = val

        # Add only whitelisted env vars
        for var in allow_env:
            val = os.environ.get(var)
            if val is not None:
                filtered_env[var] = val

        # Ensure Python uses UTF-8 in the subprocess
        filtered_env["PYTHONIOENCODING"] = "utf-8"
        filtered_env["PYTHONUTF8"] = "1"

        # Pass chat path as env var so it doesn't interfere with script's own argv
        filtered_env["THREADBEAR_CHAT_PATH"] = chat_path
        filtered_env["THREADBEAR_CHAT_FILE"] = os.path.basename(chat_path)

        try:
            result = subprocess.run(
                [sys.executable, script_path],
                env=filtered_env,
                timeout=timeout,
                cwd=project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Script timed out after {timeout} seconds",
                "returncode": -1,
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "returncode": -1,
            }


def default_permissions(scan_result: dict) -> dict:
    """Generate conservative default permissions from a scan result.

    Scripts get only what they demonstrably need, nothing more.
    """
    caps = scan_result.get("capabilities", [])
    env_vars = scan_result.get("env_vars", [])

    return {
        "allow_env": env_vars,  # Only vars the script actually references
        "allow_paths": [],
        "allow_network": "network" in caps,
        "allow_subprocess": "subprocess" in caps,
        "timeout": 60,
        "scanned": True,
        "scan_result": scan_result,
    }


def permissive_defaults() -> dict:
    """Permissive defaults for migrated scripts that haven't been scanned yet."""
    return {
        "allow_env": [],
        "allow_paths": [],
        "allow_network": True,
        "allow_subprocess": True,
        "timeout": 60,
        "scanned": False,
        "scan_result": None,
    }
