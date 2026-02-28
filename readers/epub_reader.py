"""
EPUB Reader for ThreadBear

Handles .epub files using ebooklib with chapter-based segmentation.
"""
from pathlib import Path
from .registry import reader_registry
from html.parser import HTMLParser


class HTMLStripper(HTMLParser):
    """Strip HTML tags from text."""
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []
    
    def handle_data(self, d):
        self.text.append(d)
    
    def get_data(self):
        return ''.join(self.text)


class EpubReader:
    """Reader for EPUB e-books."""

    @staticmethod
    def extract_text(path):
        """Extract text from all chapters."""
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError("EPUB reading requires ebooklib: pip install ebooklib")
        
        book = epub.read_epub(str(path))
        parts = []
        
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                # Extract text from HTML content
                stripper = HTMLStripper()
                try:
                    stripper.feed(item.get_content().decode('utf-8', errors='replace'))
                    text = stripper.get_data()
                    if text.strip():
                        parts.append(text)
                except Exception:
                    pass
        
        return '\n\n'.join(parts)

    @staticmethod
    def extract_segments(path):
        """Chapter-based segmentation."""
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError("EPUB reading requires ebooklib: pip install ebooklib")
        
        book = epub.read_epub(str(path))
        segments = []
        
        for i, item in enumerate(book.get_items()):
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                # Extract text from HTML content
                stripper = HTMLStripper()
                try:
                    stripper.feed(item.get_content().decode('utf-8', errors='replace'))
                    text = stripper.get_data()
                    if text.strip():
                        segments.append({
                            'text': text,
                            'start': i,
                            'end': i + 1,
                            'tokens': len(text) // 4,
                            'label': f'Chapter {len(segments) + 1}'
                        })
                except Exception:
                    pass
        
        return segments


reader_registry.register(['.epub'], EpubReader, requires=['ebooklib'])
