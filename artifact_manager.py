"""
Artifact Manager for ThreadBear

Manages artifact lifecycle: creation, storage, flow between branches.
Artifacts are stored in: artifacts/{artifact_id}/
  - metadata.json (type, producer, hash, tags)
  - content file (the actual data, named after the artifact)

Design principle: No silent context leakage.
Artifacts only flow via explicit artifact_flow edges.
"""
import json
import hashlib
from uuid import uuid4
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


class ArtifactManager:
    """
    Manages artifact lifecycle: creation, storage, flow between branches.
    """

    VALID_TYPES = ('document', 'code', 'image', 'data', 'summary')
    TYPE_EXTENSIONS = {
        'document': '.md', 'code': '.txt', 'image': '.png',
        'data': '.json', 'summary': '.md'
    }

    def __init__(self, db, storage_dir: str = 'artifacts'):
        self.db = db  # BranchDatabase instance
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)

    def create_artifact(self, branch_id: str, artifact_type: str,
                        content: Any, name: str = None,
                        tags: List[str] = None) -> Dict:
        """
        Create an artifact from branch output.

        Args:
            branch_id: producing branch
            artifact_type: one of VALID_TYPES
            content: str or bytes
            name: human-readable name (auto-generated if None)
            tags: list of tag strings for search/filtering

        Returns: artifact dict with id, path, hash, metadata
        """
        if artifact_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid artifact type: {artifact_type}. Must be one of {self.VALID_TYPES}")

        artifact_id = str(uuid4())
        content_bytes = content.encode('utf-8') if isinstance(content, str) else content
        content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]

        # Store on filesystem
        artifact_dir = self.storage_dir / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        filename = name or f'artifact{self.TYPE_EXTENSIONS.get(artifact_type, ".txt")}'
        content_path = artifact_dir / filename
        if isinstance(content, str):
            content_path.write_text(content, encoding='utf-8')
        else:
            content_path.write_bytes(content)

        # Save metadata alongside content
        meta = {
            'id': artifact_id,
            'type': artifact_type,
            'name': name or filename,
            'producer_branch_id': branch_id,
            'hash': content_hash,
            'tags': tags or [],
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        (artifact_dir / 'metadata.json').write_text(
            json.dumps(meta, indent=2), encoding='utf-8'
        )

        # Store in DB
        conn = self.db._get_connection()
        try:
            conn.execute("""
                INSERT INTO artifacts (id, type, producer_branch_id, name, hash, tags, path, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [artifact_id, artifact_type, branch_id, name or filename,
                  content_hash, json.dumps(tags or []), str(content_path),
                  json.dumps(meta), datetime.now(timezone.utc).isoformat()])
            conn.commit()
        finally:
            conn.close()

        return meta

    def get_artifact(self, artifact_id: str) -> Optional[Dict]:
        """Get artifact metadata + content."""
        conn = self.db._get_connection()
        try:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", [artifact_id]).fetchone()
            if not row:
                return None
            artifact = dict(row)
            try:
                artifact['content'] = Path(artifact['path']).read_text(encoding='utf-8')
                artifact['is_binary'] = False
            except UnicodeDecodeError:
                artifact['content'] = f'[Binary content at {artifact["path"]}]'
                artifact['is_binary'] = True
            return artifact
        finally:
            conn.close()

    def list_branch_artifacts(self, branch_id: str,
                              include_incoming: bool = True) -> Dict:
        """
        List artifacts for a branch.
        include_incoming=True also returns artifacts that have been
        flowed TO this branch via artifact_flow edges.
        """
        conn = self.db._get_connection()
        try:
            # Produced by this branch
            produced = [dict(r) for r in conn.execute(
                "SELECT * FROM artifacts WHERE producer_branch_id = ? ORDER BY created_at DESC",
                [branch_id]
            ).fetchall()]

            if not include_incoming:
                return {'produced': produced, 'incoming': []}

            # Flowed to this branch via artifact_flow edges
            incoming_edges = [dict(r) for r in conn.execute("""
                SELECT e.payload FROM edges e
                WHERE e.to_branch_id = ? AND e.type = 'artifact_flow'
            """, [branch_id]).fetchall()]

            incoming_ids = set()
            for edge in incoming_edges:
                payload = json.loads(edge.get('payload', '{}'))
                incoming_ids.update(payload.get('artifact_ids', []))

            incoming = []
            for aid in incoming_ids:
                artifact = conn.execute("SELECT * FROM artifacts WHERE id = ?", [aid]).fetchone()
                if artifact:
                    incoming.append(dict(artifact))

            return {'produced': produced, 'incoming': incoming}
        finally:
            conn.close()

    def flow_artifact(self, artifact_id: str, from_branch_id: str,
                      to_branch_id: str) -> bool:
        """
        Flow an artifact from one branch to another.
        Creates or updates an artifact_flow edge.
        Requires explicit action — no silent context leakage.
        """
        conn = self.db._get_connection()
        try:
            # Verify artifact exists
            artifact = conn.execute("SELECT * FROM artifacts WHERE id = ?", [artifact_id]).fetchone()
            if not artifact:
                raise ValueError(f"Artifact {artifact_id} not found")

            # Check for existing artifact_flow edge
            existing = conn.execute("""
                SELECT * FROM edges
                WHERE from_branch_id = ? AND to_branch_id = ? AND type = 'artifact_flow'
            """, [from_branch_id, to_branch_id]).fetchone()

            if existing:
                # Add artifact to existing edge
                payload = json.loads(existing['payload'])
                if artifact_id not in payload.get('artifact_ids', []):
                    payload.setdefault('artifact_ids', []).append(artifact_id)
                    conn.execute("UPDATE edges SET payload = ? WHERE id = ?",
                                [json.dumps(payload), existing['id']])
                    conn.commit()
            else:
                # Create new artifact_flow edge
                self.db.create_edge(from_branch_id, to_branch_id, 'artifact_flow', {
                    'artifact_ids': [artifact_id]
                })

            return True
        finally:
            conn.close()

    def search_artifacts(self, query: str = None, artifact_type: str = None,
                         tags: List[str] = None) -> List[Dict]:
        """Search artifacts across all branches."""
        conn = self.db._get_connection()
        try:
            conditions = []
            params = []

            if artifact_type:
                conditions.append("type = ?")
                params.append(artifact_type)
            if query:
                conditions.append("(name LIKE ? OR tags LIKE ?)")
                params.extend([f'%{query}%', f'%{query}%'])
            if tags:
                for tag in tags:
                    conditions.append("tags LIKE ?")
                    params.append(f'%"{tag}"%')

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"SELECT * FROM artifacts {where} ORDER BY created_at DESC",
                params
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete an artifact (both DB and filesystem)."""
        artifact = self.get_artifact(artifact_id)
        if not artifact:
            return False

        # Delete from filesystem
        try:
            content_path = Path(artifact['path'])
            if content_path.exists():
                content_path.unlink()
            metadata_path = content_path.parent / 'metadata.json'
            if metadata_path.exists():
                metadata_path.unlink()
            content_path.parent.rmdir()
        except Exception:
            pass  # Continue with DB deletion even if file deletion fails

        # Delete from DB
        conn = self.db._get_connection()
        try:
            conn.execute("DELETE FROM artifacts WHERE id = ?", [artifact_id])
            conn.commit()
            return True
        finally:
            conn.close()
