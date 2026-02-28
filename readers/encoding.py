"""
Encoding normalization for document ingestion.

Detects and normalizes file encoding to UTF-8.
"""
from pathlib import Path


def normalize_encoding(file_path):
    """
    Detect and normalize file encoding to UTF-8 string.
    Uses charset_normalizer for reliable detection.
    Falls back to UTF-8 with replace on import failure.
    """
    try:
        from charset_normalizer import from_path
        result = from_path(file_path)
        best = result.best()
        if best is None:
            # Fallback
            return Path(file_path).read_text(encoding='utf-8', errors='replace')
        return str(best)
    except ImportError:
        return Path(file_path).read_text(encoding='utf-8', errors='replace')
