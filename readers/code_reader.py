"""
Code Reader for ThreadBear

Handles 20+ programming languages with function/class-based segmentation.
Wraps code in markdown code blocks for better LLM consumption.
"""
import re
from pathlib import Path
from .registry import reader_registry
from .encoding import normalize_encoding


class CodeReader:
    """Reader for source code files."""

    LANG_MAP = {
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
        '.jsx': 'javascript', '.tsx': 'typescript',
        '.html': 'html', '.css': 'css', '.json': 'json',
        '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
        '.cpp': 'cpp', '.c': 'c', '.h': 'cpp', '.hpp': 'cpp',
        '.java': 'java', '.rs': 'rust', '.go': 'go',
        '.sh': 'bash', '.bat': 'batch', '.ps1': 'powershell',
        '.sql': 'sql', '.xml': 'xml', '.rb': 'ruby', '.php': 'php',
        '.swift': 'swift', '.kt': 'kotlin', '.scala': 'scala',
        '.r': 'r', '.lua': 'lua', '.perl': 'perl', '.pl': 'perl',
    }

    # Regex patterns for function/class extraction by language
    SEGMENT_PATTERNS = {
        'python': r'^(class\s+\w+|def\s+\w+|async\s+def\s+\w+)',
        'javascript': r'^(function\s+\w+|class\s+\w+|const\s+\w+\s*=|let\s+\w+\s*=|var\s+\w+\s*=|export\s+(default\s+)?(function|class|const|let))',
        'typescript': r'^(function\s+\w+|class\s+\w+|const\s+\w+|let\s+\w+|interface\s+\w+|type\s+\w+|enum\s+\w+|export)',
        'rust': r'^(fn\s+\w+|struct\s+\w+|enum\s+\w+|impl\s+|trait\s+\w+)',
        'go': r'^(func\s+\w+|type\s+\w+\s+struct|type\s+\w+\s+interface)',
        'java': r'^(\s*(public|private|protected|static|\s)+\s+(class|interface|void|int|String|boolean|double|float|long|short|byte|char)\s+\w+)',
        'cpp': r'^(void|int|float|double|char|class|struct|template|namespace\s+\w+)',
        'c': r'^(void|int|float|double|char|struct|typedef)',
        'ruby': r'^(def\s+\w+|class\s+\w+|module\s+\w+)',
        'php': r'^(function\s+\w+|class\s+\w+|public\s+function|private\s+function)',
        'swift': r'^(func\s+\w+|class\s+\w+|struct\s+\w+|enum\s+\w+|protocol\s+\w+)',
        'kotlin': r'^(fun\s+\w+|class\s+\w+|interface\s+\w+|object\s+\w+)',
        'scala': r'^(def\s+\w+|class\s+\w+|object\s+\w+|trait\s+\w+)',
        'r': r'^(function\s*\(|\w+\s*<-\s*function)',
        'lua': r'^(function\s+\w+|local\s+function\s+\w+)',
        'perl': r'^(sub\s+\w+)',
        'powershell': r'^(function\s+\w+|filter\s+\w+)',
        'sql': r'^(CREATE|ALTER|DROP|SELECT|INSERT|UPDATE|DELETE|WITH|GO)',
    }

    @staticmethod
    def extract_text(path):
        """Wrap code in markdown code block with language tag."""
        ext = Path(path).suffix.lower()
        lang = CodeReader.LANG_MAP.get(ext, '')
        text = normalize_encoding(path)
        return f"```{lang}\n{text}\n```"

    @staticmethod
    def extract_segments(path):
        """
        Segment by logical units (functions/classes).
        Falls back to fixed-size line chunks if no pattern matches.
        """
        ext = Path(path).suffix.lower()
        lang = CodeReader.LANG_MAP.get(ext, '')
        text = normalize_encoding(path)
        lines = text.split('\n')

        pattern = CodeReader.SEGMENT_PATTERNS.get(lang)
        if not pattern:
            # Fallback: chunk by 50 lines
            return CodeReader._chunk_by_lines(lines, lang, 50)

        # Find segment boundaries
        boundaries = []
        for i, line in enumerate(lines):
            if re.match(pattern, line.strip()):
                boundaries.append(i)

        if not boundaries:
            return CodeReader._chunk_by_lines(lines, lang, 50)

        # Build segments from boundaries
        segments = []
        for idx, start in enumerate(boundaries):
            end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
            segment_lines = lines[start:end]
            text_block = '\n'.join(segment_lines)
            label = segment_lines[0].strip()[:60]
            segments.append({
                'text': f"```{lang}\n{text_block}\n```",
                'start': start,
                'end': end,
                'tokens': len(text_block) // 4,
                'label': label
            })
        return segments

    @staticmethod
    def _chunk_by_lines(lines, lang, chunk_size):
        """Fallback: chunk by fixed number of lines."""
        segments = []
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i:i + chunk_size]
            text = '\n'.join(chunk)
            segments.append({
                'text': f"```{lang}\n{text}\n```",
                'start': i,
                'end': i + len(chunk),
                'tokens': len(text) // 4,
                'label': f'Lines {i + 1}-{i + len(chunk)}'
            })
        return segments


reader_registry.register(list(CodeReader.LANG_MAP.keys()), CodeReader)
