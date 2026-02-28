"""
ThreadBear Reader Package

Document readers with registry pattern for extensibility.
"""
from .registry import reader_registry, ReaderRegistry

__all__ = ['reader_registry', 'ReaderRegistry']

# Auto-discover readers on package import
reader_registry.auto_discover()
