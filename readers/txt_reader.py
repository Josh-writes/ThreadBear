"""
Plain Text Reader for ThreadBear

Handles .txt, .text, .log files with encoding detection.
"""
from pathlib import Path
from .registry import reader_registry
from .encoding import normalize_encoding


class TxtReader:
    """Reader for plain text files."""

    @staticmethod
    def extract_text(path):
        """Extract full text from file, handling encoding."""
        return normalize_encoding(path)

    @staticmethod
    def extract_segments(path):
        """Segment by paragraphs (double newlines)."""
        text = TxtReader.extract_text(path)
        paragraphs = text.split('\n\n')
        segments = []
        pos = 0
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                pos += 2
                continue
            try:
                start = text.index(para, pos) if para in text[pos:] else pos
            except ValueError:
                start = pos
            segments.append({
                'text': para,
                'start': start,
                'end': start + len(para),
                'tokens': len(para) // 4,
                'label': f'Paragraph {i + 1}'
            })
            pos = start + len(para)
        return segments


reader_registry.register(['.txt', '.text', '.log'], TxtReader)
