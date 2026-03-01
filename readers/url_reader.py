"""
URL Reader for ThreadBear

Converts web pages to clean Markdown using the mark-it-down tool
(web-to-markdown Node.js CLI). Falls back to requests if unavailable.
"""
import os
import re
import subprocess
from pathlib import Path

from .registry import reader_registry

# Resolve path to the web-to-markdown CLI relative to project root
_PROJECT_ROOT = Path(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
_MARKITDOWN_CLI = _PROJECT_ROOT / '_workspace' / 'mark-it-down' / 'dist' / 'bin' / 'web-to-markdown.js'


def _run_markitdown(url: str, timeout: int = 30) -> str:
    """Run web-to-markdown CLI and return the Markdown output."""
    result = subprocess.run(
        ['node', str(_MARKITDOWN_CLI), url, '--timeout', str(timeout * 1000)],
        capture_output=True, text=True, timeout=timeout + 5,
        cwd=str(_PROJECT_ROOT),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"web-to-markdown failed: {stderr or 'unknown error'}")
    return result.stdout


class UrlReader:
    """Reader for web URLs — converts pages to clean Markdown."""

    @staticmethod
    def extract_text(url: str) -> str:
        """Fetch URL, convert to clean Markdown text."""
        if not _MARKITDOWN_CLI.exists():
            raise RuntimeError(
                "web-to-markdown not built. Run: cd _workspace/mark-it-down && npm install && npm run build"
            )
        text = _run_markitdown(url)
        if not text or not text.strip():
            raise RuntimeError(f"No content extracted from {url}")
        return text

    @staticmethod
    def extract_segments(url: str) -> list:
        """Split Markdown output into segments by headings."""
        if not _MARKITDOWN_CLI.exists():
            raise RuntimeError(
                "web-to-markdown not built. Run: cd _workspace/mark-it-down && npm install && npm run build"
            )
        text = _run_markitdown(url)
        if not text or not text.strip():
            return []

        # Split on markdown headings (# through ######)
        segments = []
        # Find all heading positions
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
        matches = list(heading_pattern.finditer(text))

        if not matches:
            # No headings — return entire content as one segment
            return [{
                'text': text.strip(),
                'start': 0,
                'end': 1,
                'tokens': len(text) // 4,
                'label': 'Web Page Content',
            }]

        for i, match in enumerate(matches):
            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section_text = text[start_pos:end_pos].strip()

            if section_text:
                segments.append({
                    'text': section_text,
                    'start': i,
                    'end': i + 1,
                    'tokens': len(section_text) // 4,
                    'label': match.group(2).strip()[:60],
                })

        return segments


# URL reader is registered separately (not by extension)
# It's used for URL ingestion, not file uploads
