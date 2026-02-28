"""
ThreadBear Branch Database

SQLite metadata index for branches. JSON files remain the source of truth
for message content; this database enables the branch DAG (Phase 2) and
cross-branch queries.

Mirrors document_db.py patterns for consistency.
"""
from __future__ import annotations
import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

from api_clients import estimate_tokens


class BranchDatabase:
    """SQLite metadata index for branches."""

    DB_PATH = "threadbear.db"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DB_PATH
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS branches (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    type TEXT DEFAULT 'chat',
                    parent_id TEXT,
                    root_id TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TEXT,
                    updated_at TEXT,
                    token_count INTEGER DEFAULT 0,
                    message_count INTEGER DEFAULT 0,
                    model TEXT,
                    provider TEXT,
                    filename TEXT,
                    context_pack_id TEXT,
                    policy TEXT,
                    metadata TEXT,
                    FOREIGN KEY (parent_id) REFERENCES branches(id)
                        ON DELETE SET NULL
                )
            """)

            # Add missing columns for Phase 2 (policy, context_pack_id)
            # SQLite doesn't support ADD COLUMN IF NOT EXISTS, so we check first
            try:
                cursor.execute("ALTER TABLE branches ADD COLUMN context_pack_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            try:
                cursor.execute("ALTER TABLE branches ADD COLUMN policy TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_branch TEXT NOT NULL,
                    to_branch TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT,
                    payload TEXT,
                    FOREIGN KEY (from_branch) REFERENCES branches(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (to_branch) REFERENCES branches(id)
                        ON DELETE CASCADE,
                    UNIQUE(from_branch, to_branch, type)
                )
            """)

            # Indexes for common queries
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_branches_parent "
                "ON branches(parent_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_branches_root "
                "ON branches(root_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_branches_status "
                "ON branches(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_branches_filename "
                "ON branches(filename)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_from "
                "ON edges(from_branch)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_to "
                "ON edges(to_branch)"
            )

    def upsert_branch(self, branch_id: str, **kwargs) -> Dict[str, Any]:
        """Insert or update a branch record.

        Accepted kwargs: title, type, parent_id, root_id, status,
        created_at, updated_at, token_count, message_count, model,
        provider, filename, context_pack_id, policy, metadata
        (dict will be JSON-encoded).

        Returns the branch dict.
        """
        if not branch_id:
            return {}

        now = datetime.now().isoformat()

        # JSON-encode metadata and policy if they're dicts
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            kwargs["metadata"] = json.dumps(kwargs["metadata"])
        if "policy" in kwargs and isinstance(kwargs["policy"], dict):
            kwargs["policy"] = json.dumps(kwargs["policy"])

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if branch exists
            existing = cursor.execute(
                "SELECT id FROM branches WHERE id = ?", (branch_id,)
            ).fetchone()

            if existing:
                # Update only provided fields
                kwargs["updated_at"] = now
                set_parts = []
                values = []
                for key, val in kwargs.items():
                    set_parts.append(f"{key} = ?")
                    values.append(val)
                values.append(branch_id)
                cursor.execute(
                    f"UPDATE branches SET {', '.join(set_parts)} "
                    f"WHERE id = ?",
                    values
                )
            else:
                # Insert new branch
                kwargs.setdefault("created_at", now)
                kwargs.setdefault("updated_at", now)
                kwargs.setdefault("status", "active")
                kwargs.setdefault("type", "chat")

                columns = ["id"] + list(kwargs.keys())
                placeholders = ["?"] * len(columns)
                values = [branch_id] + list(kwargs.values())
                cursor.execute(
                    f"INSERT INTO branches ({', '.join(columns)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    values
                )

        return self.get_branch(branch_id) or {}

    def get_branch(self, branch_id: str) -> Optional[Dict[str, Any]]:
        """Get a single branch by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM branches WHERE id = ?", (branch_id,)
            ).fetchone()
            if row:
                return self._row_to_dict(row)
            return None

    def get_branch_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get a branch by its JSON filename."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM branches WHERE filename = ?", (filename,)
            ).fetchone()
            if row:
                return self._row_to_dict(row)
            return None

    def get_children(self, branch_id: str) -> List[Dict[str, Any]]:
        """Get direct children of a branch."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM branches WHERE parent_id = ? "
                "ORDER BY created_at",
                (branch_id,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_tree(self, root_id: str) -> List[Dict[str, Any]]:
        """Get all branches under a root, for tree rendering."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM branches WHERE root_id = ? "
                "ORDER BY created_at",
                (root_id,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def list_branches(self, type: Optional[str] = None, status: Optional[str] = None,
                      parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List branches with optional filters."""
        conditions = []
        params: List[Any] = []

        if type:
            conditions.append("type = ?")
            params.append(type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if parent_id is not None:
            conditions.append("parent_id = ?")
            params.append(parent_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            query_str = f"SELECT * FROM branches {where} ORDER BY created_at"
            rows = conn.execute(query_str, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def search_branches(self, query: Optional[str] = None,
                        status: Optional[str] = None,
                        branch_type: Optional[str] = None,
                        limit: int = 100,
                        offset: int = 0) -> List[Dict[str, Any]]:
        """Search branches with optional filters."""
        conditions = []
        params: List[Any] = []

        if query:
            conditions.append("title LIKE ?")
            params.append(f"%{query}%")
        if status:
            conditions.append("status = ?")
            params.append(status)
        if branch_type:
            conditions.append("type = ?")
            params.append(branch_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM branches {where} "
                f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def add_edge(self, from_branch: str, to_branch: str,
                 edge_type: str, payload: Optional[Dict] = None) -> None:
        """Add an edge between two branches."""
        now = datetime.now().isoformat()
        payload_json = json.dumps(payload) if payload else None

        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO edges "
                "(from_branch, to_branch, type, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (from_branch, to_branch, edge_type, now, payload_json)
            )

    def get_edges(self, branch_id: str,
                  direction: str = "both") -> List[Dict[str, Any]]:
        """Get edges for a branch. direction: 'from', 'to', or 'both'."""
        results = []
        with self._get_connection() as conn:
            if direction in ("from", "both"):
                rows = conn.execute(
                    "SELECT * FROM edges WHERE from_branch = ?",
                    (branch_id,)
                ).fetchall()
                results.extend(self._row_to_dict(r) for r in rows)
            if direction in ("to", "both"):
                rows = conn.execute(
                    "SELECT * FROM edges WHERE to_branch = ?",
                    (branch_id,)
                ).fetchall()
                results.extend(self._row_to_dict(r) for r in rows)
        return results

    def delete_branch(self, branch_id: str) -> bool:
        """Delete a branch and its edges (CASCADE)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM branches WHERE id = ?", (branch_id,)
            )
            return cursor.rowcount > 0

    def migrate_from_json(self, chats_dir: str = "chats") -> int:
        """Scan chats/ directory and create branch records from JSON metadata.

        Two-pass:
        1. Create all branch records
        2. Create parent_of edges where parent_chat_id is set

        Returns the number of branches migrated.
        """
        if not os.path.isdir(chats_dir):
            return 0

        migrated = 0
        branches_data: List[Dict[str, Any]] = []

        # Pass 1: scan JSON files, create branch records
        for fn in os.listdir(chats_dir):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(chats_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, list):
                    # Old list format — skip, should have been migrated already
                    continue

                chat_id = data.get("chat_id")
                if not chat_id:
                    continue

                # Check if already in DB
                existing = self.get_branch(chat_id)
                if existing:
                    continue

                hist = data.get("chat_history", [])
                token_count = data.get("token_count", 0)
                if not token_count and hist:
                    token_count = sum(
                        estimate_tokens(m.get("content", "")) for m in hist
                    )

                # Get file modified time for created_at
                mtime = os.path.getmtime(path)
                created_at = datetime.fromtimestamp(mtime).isoformat()

                branch_info = {
                    "id": chat_id,
                    "title": data.get("title", fn[:-5].replace("_", " ")),
                    "type": "chat",
                    "parent_id": data.get("parent_chat_id") or None,
                    "root_id": data.get("root_chat_id") or chat_id,
                    "token_count": token_count,
                    "message_count": len(hist),
                    "filename": fn,
                    "created_at": created_at,
                }

                branches_data.append(branch_info)

            except Exception as e:
                print(f"[BranchDB] Error reading {fn} for migration: {e}")

        # Insert all branches (parent_id FK may not resolve yet, that's OK)
        for bd in branches_data:
            branch_id = bd.pop("id")
            # parent_id might reference a branch not yet inserted;
            # store it but the FK is ON DELETE SET NULL so it's safe
            parent_id = bd.pop("parent_id", None)
            self.upsert_branch(branch_id, **bd)
            migrated += 1

        # Pass 2: set parent_id and create parent_of edges
        for bd_orig in branches_data:
            # Re-read to get the parent info we popped
            pass

        # Re-scan for parent relationships
        for fn in os.listdir(chats_dir):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(chats_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue

                chat_id = data.get("chat_id")
                parent_id = data.get("parent_chat_id")
                if chat_id and parent_id:
                    # Update parent_id on the branch
                    self.upsert_branch(chat_id, parent_id=parent_id)
                    # Create edge
                    self.add_edge(parent_id, chat_id, "parent_of")
            except Exception:
                pass

        if migrated > 0:
            print(f"[BranchDB] Migrated {migrated} branches from JSON files")
        return migrated

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite3.Row to a plain dict."""
        d = dict(row)
        # Parse metadata JSON if present
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        # Parse payload JSON for edges
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d
