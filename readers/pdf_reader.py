"""
PDF Reader for ThreadBear

Handles .pdf files using pypdf with page-based segmentation.
"""
from pathlib import Path
from .registry import reader_registry


class PdfReader:
    """Reader for PDF files."""

    @staticmethod
    def extract_text(path):
        """Extract text from PDF using pypdf."""
        try:
            from pypdf import PdfReader as PyPdfReader
        except ImportError:
            raise ImportError("PDF reading requires pypdf: pip install pypdf")
        
        reader = PyPdfReader(str(path))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text)
        return '\n\n'.join(texts)

    @staticmethod
    def extract_segments(path):
        """Segment by page."""
        try:
            from pypdf import PdfReader as PyPdfReader
        except ImportError:
            raise ImportError("PDF reading requires pypdf: pip install pypdf")
        
        reader = PyPdfReader(str(path))
        segments = []
        
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                segments.append({
                    'text': text,
                    'start': i,
                    'end': i + 1,
                    'tokens': len(text) // 4,
                    'label': f'Page {i + 1}'
                })
        
        return segments


reader_registry.register(['.pdf'], PdfReader, requires=['pypdf'])
