"""
ThreadBear Reader Registry

Dynamic reader registration for document ingestion.
Readers register by extension with graceful dependency handling.
"""


class ReaderRegistry:
    """
    Dynamic reader registration replacing hardcoded reader dict.
    Readers register by extension. Missing dependencies handled gracefully.
    """

    def __init__(self):
        self._readers = {}     # ext (lowercase, with dot) -> reader info
        self._available = {}   # ext -> bool (True if dependencies installed)

    def register(self, extensions, reader_class, requires=None):
        """
        Register a reader for file extensions.

        Args:
            extensions: list of extensions (e.g., ['.csv', '.tsv'])
            reader_class: class with:
                - extract_text(path) -> str
                - extract_segments(path) -> list[{text, start, end, tokens, label}]
            requires: optional list of package names to verify importability
        """
        available = True
        missing = []
        if requires:
            for pkg in requires:
                try:
                    __import__(pkg)
                except ImportError:
                    available = False
                    missing.append(pkg)

        for ext in extensions:
            ext = ext.lower() if ext.startswith('.') else f'.{ext.lower()}'
            self._readers[ext] = {
                'class': reader_class,
                'available': available,
                'missing_deps': missing
            }

    def get_reader(self, extension):
        """Get reader class for extension. Returns None if unavailable."""
        ext = extension.lower() if extension.startswith('.') else f'.{extension.lower()}'
        entry = self._readers.get(ext)
        if not entry or not entry['available']:
            return None
        return entry['class']

    def supported_extensions(self):
        """List all registered extensions with availability info."""
        result = {}
        for ext, entry in self._readers.items():
            result[ext] = {
                'available': entry['available'],
                'reader': entry['class'].__name__,
                'missing_deps': entry.get('missing_deps', [])
            }
        return result

    def auto_discover(self):
        """Import all reader modules to trigger self-registration."""
        # Core readers (no external deps)
        from . import txt_reader, md_reader, csv_reader, code_reader

        # Readers with external deps (graceful failure)
        try:
            from . import pdf_reader
        except ImportError:
            pass
        try:
            from . import docx_reader
        except ImportError:
            pass
        self._try_import('excel_reader', ['openpyxl'])
        self._try_import('pptx_reader', ['pptx'])
        self._try_import('epub_reader', ['ebooklib'])
        self._try_import('url_reader', ['requests', 'bs4'])

    def _try_import(self, module_name, requires=None):
        """Import a reader module, silently skip if dependencies missing."""
        try:
            __import__(f'readers.{module_name}', fromlist=[''])
        except ImportError:
            pass


reader_registry = ReaderRegistry()
