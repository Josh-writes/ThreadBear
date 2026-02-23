"""
ThreadBear Document Database Manager

SQLite-based storage for document metadata, sections, highlights, summaries, and tags.
"""
from __future__ import annotations
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager


class DocumentDatabase:
    def __init__(self, db_path: str = "threadbear_docs.db"):
        self.db_path = db_path
        self._init_database()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Return rows as dicts
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_database(self):
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Core document info
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    file_type TEXT,
                    hash TEXT,
                    total_tokens INTEGER,
                    analysis_level TEXT DEFAULT 'quick',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

            # Auto-detected sections (chapters, pages, headers)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    idx INTEGER,
                    title TEXT,
                    start_pos INTEGER,
                    end_pos INTEGER,
                    tokens INTEGER,
                    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # AI-generated section summaries
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS section_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_id INTEGER NOT NULL,
                    summary TEXT,
                    tokens INTEGER,
                    model TEXT,
                    created_at TEXT,
                    FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE
                )
            """)

            # User text selections (highlights)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS highlights (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    start_pos INTEGER,
                    end_pos INTEGER,
                    label TEXT,
                    tokens INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # Document-level summaries and analysis
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS doc_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    summary_type TEXT,
                    content TEXT,
                    tokens INTEGER,
                    model TEXT,
                    created_at TEXT,
                    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # Tags (auto-generated or user-added)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    tag TEXT,
                    source TEXT,
                    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # Track what's currently selected for context
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS context_selections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    selection_type TEXT,
                    selection_id TEXT,
                    selected INTEGER DEFAULT 1,
                    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # Create indexes for common queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(doc_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_highlights_doc ON highlights(doc_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_doc ON tags(doc_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_summaries_doc ON doc_summaries(doc_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_selections_doc ON context_selections(doc_id)")

            # Enable foreign key support
            cursor.execute("PRAGMA foreign_keys = ON")

    # ========== Document CRUD ==========

    def add_document(self, doc_id: str, name: str, file_type: str,
                     hash: str, total_tokens: int) -> bool:
        """Add a new document to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO documents (id, name, file_type, hash, total_tokens,
                                       analysis_level, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'quick', ?, ?)
            """, (doc_id, name, file_type, hash, total_tokens, now, now))
            return True

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get document by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM documents ORDER BY updated_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def update_document(self, doc_id: str, **kwargs) -> bool:
        """Update document fields."""
        if not kwargs:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
            values = list(kwargs.values()) + [datetime.now().isoformat(), doc_id]
            cursor.execute(f"""
                UPDATE documents SET {fields}, updated_at = ? WHERE id = ?
            """, values)
            return cursor.rowcount > 0

    def delete_document(self, doc_id: str) -> bool:
        """Delete document and all related data (cascades)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            return cursor.rowcount > 0

    # ========== Sections ==========

    def add_section(self, doc_id: str, idx: int, title: str,
                    start_pos: int, end_pos: int, tokens: int) -> int:
        """Add a section to a document. Returns section ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sections (doc_id, idx, title, start_pos, end_pos, tokens)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (doc_id, idx, title, start_pos, end_pos, tokens))
            return cursor.lastrowid

    def get_sections(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all sections for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.*, ss.summary, ss.tokens as summary_tokens, ss.model as summary_model
                FROM sections s
                LEFT JOIN section_summaries ss ON s.id = ss.section_id
                WHERE s.doc_id = ?
                ORDER BY s.idx
            """, (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_sections(self, doc_id: str) -> bool:
        """Clear all sections for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sections WHERE doc_id = ?", (doc_id,))
            return True

    # ========== Section Summaries ==========

    def add_section_summary(self, section_id: int, summary: str,
                            tokens: int, model: str) -> int:
        """Add a summary for a section. Returns summary ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO section_summaries (section_id, summary, tokens, model, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (section_id, summary, tokens, model, now))
            return cursor.lastrowid

    def get_section_summary(self, section_id: int) -> Optional[Dict[str, Any]]:
        """Get summary for a section."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM section_summaries WHERE section_id = ?
            """, (section_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ========== Highlights ==========

    def add_highlight(self, highlight_id: str, doc_id: str, start_pos: int,
                      end_pos: int, label: str, tokens: int) -> bool:
        """Add a user highlight/selection."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO highlights (id, doc_id, start_pos, end_pos, label, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (highlight_id, doc_id, start_pos, end_pos, label, tokens, now))
            return True

    def get_highlights(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all highlights for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM highlights WHERE doc_id = ? ORDER BY start_pos
            """, (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_highlight(self, highlight_id: str) -> bool:
        """Delete a highlight."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
            return cursor.rowcount > 0

    # ========== Document Summaries ==========

    def add_doc_summary(self, doc_id: str, summary_type: str, content: str,
                        tokens: int, model: str) -> int:
        """Add a document-level summary. Returns summary ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO doc_summaries (doc_id, summary_type, content, tokens, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (doc_id, summary_type, content, tokens, model, now))
            return cursor.lastrowid

    def get_doc_summaries(self, doc_id: str, summary_type: str = None) -> List[Dict[str, Any]]:
        """Get document summaries, optionally filtered by type."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if summary_type:
                cursor.execute("""
                    SELECT * FROM doc_summaries WHERE doc_id = ? AND summary_type = ?
                    ORDER BY created_at DESC
                """, (doc_id, summary_type))
            else:
                cursor.execute("""
                    SELECT * FROM doc_summaries WHERE doc_id = ?
                    ORDER BY created_at DESC
                """, (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    # ========== Tags ==========

    def add_tag(self, doc_id: str, tag: str, source: str = 'user') -> int:
        """Add a tag to a document. Returns tag ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Check if tag already exists
            cursor.execute("""
                SELECT id FROM tags WHERE doc_id = ? AND tag = ?
            """, (doc_id, tag))
            if cursor.fetchone():
                return -1  # Tag already exists
            cursor.execute("""
                INSERT INTO tags (doc_id, tag, source)
                VALUES (?, ?, ?)
            """, (doc_id, tag, source))
            return cursor.lastrowid

    def get_tags(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all tags for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tags WHERE doc_id = ? ORDER BY tag
            """, (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_tag(self, doc_id: str, tag: str) -> bool:
        """Delete a tag from a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tags WHERE doc_id = ? AND tag = ?", (doc_id, tag))
            return cursor.rowcount > 0

    def get_documents_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """Find all documents with a specific tag."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT d.* FROM documents d
                JOIN tags t ON d.id = t.doc_id
                WHERE t.tag = ?
                ORDER BY d.updated_at DESC
            """, (tag,))
            return [dict(row) for row in cursor.fetchall()]

    # ========== Context Selections ==========

    def set_selection(self, doc_id: str, selection_type: str,
                      selection_id: str, selected: bool) -> bool:
        """Set selection state for a document item."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Delete existing then insert (simpler than upsert)
            cursor.execute("""
                DELETE FROM context_selections
                WHERE doc_id = ? AND selection_type = ? AND selection_id = ?
            """, (doc_id, selection_type, selection_id))

            if selected:
                cursor.execute("""
                    INSERT INTO context_selections (doc_id, selection_type, selection_id, selected)
                    VALUES (?, ?, ?, 1)
                """, (doc_id, selection_type, selection_id))
            return True

    def get_selections(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all selections for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM context_selections WHERE doc_id = ? AND selected = 1
            """, (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_selections(self, doc_id: str) -> bool:
        """Clear all selections for a document."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM context_selections WHERE doc_id = ?", (doc_id,))
            return True

    # ========== Utility Methods ==========

    def get_full_document_data(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get complete document data including sections, highlights, summaries, tags."""
        doc = self.get_document(doc_id)
        if not doc:
            return None

        doc['sections'] = self.get_sections(doc_id)
        doc['highlights'] = self.get_highlights(doc_id)
        doc['summaries'] = self.get_doc_summaries(doc_id)
        doc['tags'] = self.get_tags(doc_id)
        doc['selections'] = self.get_selections(doc_id)

        return doc

    def get_all_tags(self) -> List[str]:
        """Get all unique tags across all documents."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT tag FROM tags ORDER BY tag")
            return [row['tag'] for row in cursor.fetchall()]


# Global instance
document_db = DocumentDatabase()
