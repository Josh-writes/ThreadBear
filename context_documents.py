"""
ThreadBear Document Context Module

Handles document ingestion, text extraction, token estimation, and context injection.
Supports .txt, .md, .pdf, .docx files with pluggable readers.
Uses SQLite for metadata storage via document_db.
"""
from __future__ import annotations
import os
import json
import uuid
import hashlib
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, NamedTuple

# Local utilities
from api_clients import estimate_tokens
from document_db import document_db

# Optional imports with fallback
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    try:
        import pypdf as PyPDF2
        PDF_AVAILABLE = True
    except ImportError:
        PDF_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


class DocumentSegment(NamedTuple):
    id: str
    label: str
    start: int
    end: int
    tokens: int


class DocumentReader:
    def extract_text(self, file_path: str) -> str:
        raise NotImplementedError

    def extract_segments(self, text: str, file_path: str | None = None) -> List[DocumentSegment]:
        paragraphs = text.split('\n\n')
        segments: List[DocumentSegment] = []
        current_pos = 0
        for i, para in enumerate(paragraphs):
            para_text = para.strip()
            if not para_text:
                current_pos += len(para) + 2
                continue
            tokens = estimate_tokens(para_text)
            segments.append(DocumentSegment(
                id=f"para_{i+1}",
                label=f"Paragraph {i+1}",
                start=current_pos,
                end=current_pos + len(para),
                tokens=tokens
            ))
            current_pos += len(para) + 2
        return segments


class TxtReader(DocumentReader):
    def extract_text(self, file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


class MarkdownReader(DocumentReader):
    def extract_text(self, file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


class PdfReader(DocumentReader):
    def extract_text(self, file_path: str) -> str:
        if not PDF_AVAILABLE:
            raise RuntimeError("PDF support not available. Install PyPDF2 or pypdf: pip install PyPDF2")
        text = ""
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text.strip():
                    text += f"\n\n--- Page {i+1} ---\n\n" + page_text
        return text.strip()

    def extract_segments(self, text: str, file_path: str | None = None) -> List[DocumentSegment]:
        segments: List[DocumentSegment] = []
        if not text:
            return segments
        pages = text.split('\n\n--- Page ')
        current_pos = 0
        for idx, page_chunk in enumerate(pages):
            chunk = page_chunk.strip()
            if not chunk:
                continue
            # Remove our header line if present
            if chunk.startswith(str(idx)) or chunk.startswith('Page '):
                lines = chunk.split('\n')
                if len(lines) > 2 and '---' in lines[0]:
                    chunk = '\n'.join(lines[2:])
            tokens = estimate_tokens(chunk)
            page_no = idx + 1
            segments.append(DocumentSegment(
                id=f"page_{page_no}",
                label=f"Page {page_no}",
                start=current_pos,
                end=current_pos + len(chunk),
                tokens=tokens,
            ))
            current_pos += len(chunk) + 4
        return segments


class DocxReader(DocumentReader):
    def extract_text(self, file_path: str) -> str:
        if not DOCX_AVAILABLE:
            raise RuntimeError("DOCX support not available. Install python-docx: pip install python-docx")
        doc = DocxDocument(file_path)
        parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        return '\n\n'.join(parts)


class ContextDocuments:
    def __init__(self, documents_dir: str = "documents"):
        self.documents_dir = Path(documents_dir)
        self.documents_dir.mkdir(exist_ok=True)

        self.readers: Dict[str, DocumentReader] = {
            'text/plain': TxtReader(),
            'text/markdown': MarkdownReader(),
            'application/pdf': PdfReader(),
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DocxReader(),
        }
        self.extension_readers: Dict[str, DocumentReader] = {
            '.txt': TxtReader(),
            '.md': MarkdownReader(),
            '.markdown': MarkdownReader(),
            '.pdf': PdfReader(),
            '.docx': DocxReader(),
        }
        self._loaded_docs: Dict[str, Dict[str, Any]] = {}

    def _reader_for(self, file_path: Path) -> DocumentReader:
        mime, _ = mimetypes.guess_type(str(file_path))
        reader = (self.readers.get(mime)
                  or self.extension_readers.get(file_path.suffix.lower()))
        if not reader:
            raise ValueError(f"Unsupported file type: {mime or file_path.suffix}")
        return reader

    def ingest_document(self, file_path: str, original_name: str | None = None) -> Dict[str, Any]:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        doc_id = str(uuid.uuid4())
        file_size = p.stat().st_size
        reader = self._reader_for(p)

        # Extract text & segments
        text = reader.extract_text(str(p))
        if not (text and text.strip()):
            raise ValueError("No text could be extracted from the document")
        segments = reader.extract_segments(text, str(p))

        # Hash original
        with open(p, 'rb') as rf:
            file_hash = hashlib.sha256(rf.read()).hexdigest()

        # Create doc folder
        doc_dir = self.documents_dir / doc_id
        doc_dir.mkdir(exist_ok=True)

        # Persist text
        (doc_dir / 'text.txt').write_text(text, encoding='utf-8')
        # Copy original
        import shutil
        shutil.copy2(p, doc_dir / f"raw{p.suffix}")

        # Get file type/mime
        mime = mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
        name = original_name or p.name
        total_tokens = estimate_tokens(text)

        # Save to SQLite database
        document_db.add_document(
            doc_id=doc_id,
            name=name,
            file_type=mime,
            hash=f"sha256:{file_hash}",
            total_tokens=total_tokens
        )

        # Save sections to database
        for i, seg in enumerate(segments):
            document_db.add_section(
                doc_id=doc_id,
                idx=i,
                title=seg.label,
                start_pos=seg.start,
                end_pos=seg.end,
                tokens=seg.tokens
            )

        # Build metadata dict for backwards compatibility
        meta = {
            "doc_id": doc_id,
            "name": name,
            "mime": mime,
            "size_bytes": file_size,
            "token_estimate_total": total_tokens,
            "segments": [
                {
                    "id": s.id,
                    "label": s.label,
                    "start": s.start,
                    "end": s.end,
                    "tokens": s.tokens,
                } for s in segments
            ],
            "highlights": [],
            "created_at": datetime.now().isoformat(),
            "hash": f"sha256:{file_hash}",
            "selected": True,
            "analysis_level": "quick"
        }

        # Also save index.json for backwards compatibility
        (doc_dir / 'index.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')

        self._loaded_docs[doc_id] = {"metadata": meta, "text": text, "segments": segments}
        return meta

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents from SQLite database."""
        db_docs = document_db.list_documents()
        docs: List[Dict[str, Any]] = []

        for doc in db_docs:
            # Build metadata dict compatible with existing code
            meta = {
                "doc_id": doc['id'],
                "name": doc['name'],
                "mime": doc.get('file_type', 'application/octet-stream'),
                "token_estimate_total": doc.get('total_tokens', 0),
                "created_at": doc.get('created_at', ''),
                "hash": doc.get('hash', ''),
                "selected": True,  # TODO: Get from context_selections
                "analysis_level": doc.get('analysis_level', 'quick')
            }
            docs.append(meta)

        return docs

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        if doc_id in self._loaded_docs:
            return self._loaded_docs[doc_id]
        d = self.documents_dir / doc_id
        index = d / 'index.json'
        textf = d / 'text.txt'
        if not (d.exists() and index.exists() and textf.exists()):
            return None
        try:
            meta = json.loads(index.read_text(encoding='utf-8'))
            text = textf.read_text(encoding='utf-8')
            segments = [DocumentSegment(**s) for s in meta.get('segments', [])]
            data = {"metadata": meta, "text": text, "segments": segments}
            self._loaded_docs[doc_id] = data
            return data
        except Exception as e:
            print(f"Error loading document {doc_id}: {e}")
            return None

    def update_document_selection(self, doc_id: str, selected: bool) -> bool:
        d = self.documents_dir / doc_id
        index = d / 'index.json'
        if not index.exists():
            return False
        try:
            meta = json.loads(index.read_text(encoding='utf-8'))
            meta['selected'] = bool(selected)
            index.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
            if doc_id in self._loaded_docs:
                self._loaded_docs[doc_id]['metadata']['selected'] = bool(selected)
            return True
        except Exception as e:
            print(f"Error updating selection for {doc_id}: {e}")
            return False

    def delete_document(self, doc_id: str) -> bool:
        """Delete document from both filesystem and SQLite."""
        d = self.documents_dir / doc_id
        success = True

        # Delete from SQLite (this cascades to sections, highlights, etc.)
        if not document_db.delete_document(doc_id):
            success = False

        # Delete from filesystem
        if d.exists():
            try:
                import shutil
                shutil.rmtree(d)
            except Exception as e:
                print(f"Error deleting document folder {doc_id}: {e}")
                success = False

        self._loaded_docs.pop(doc_id, None)
        return success

    def add_highlight(self, doc_id: str, start: int, end: int, label: str | None = None) -> Optional[str]:
        doc = self.get_document(doc_id)
        if not doc:
            return None
        text = doc['text']
        if start < 0 or end > len(text) or start >= end:
            return None
        hid = str(uuid.uuid4())[:8]
        snippet = text[start:end]
        tokens = estimate_tokens(snippet)
        highlight_label = label or f"Selection {hid}"

        # Save to SQLite
        document_db.add_highlight(
            highlight_id=hid,
            doc_id=doc_id,
            start_pos=start,
            end_pos=end,
            label=highlight_label,
            tokens=tokens
        )

        # Also update in-memory and JSON for backwards compatibility
        meta = doc['metadata']
        meta.setdefault('highlights', []).append({
            "id": hid,
            "start": start,
            "end": end,
            "tokens": tokens,
            "label": highlight_label,
        })
        (self.documents_dir / doc_id / 'index.json').write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        return hid

    def remove_highlight(self, doc_id: str, highlight_id: str) -> bool:
        # Delete from SQLite
        document_db.delete_highlight(highlight_id)

        # Also update JSON for backwards compatibility
        doc = self.get_document(doc_id)
        if not doc:
            return True  # Already deleted from DB
        meta = doc['metadata']
        hs = meta.get('highlights', [])
        for i, h in enumerate(hs):
            if h.get('id') == highlight_id:
                hs.pop(i)
                (self.documents_dir / doc_id / 'index.json').write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8'
                )
                break
        return True

    def build_context_injections(self, selected_docs: List[str] | None = None,
                                 selected_spans: Dict[str, List[str]] | None = None) -> List[Dict[str, str]]:
        if selected_docs is None:
            selected_docs = [m['doc_id'] for m in self.list_documents() if m.get('selected')]
        msgs: List[Dict[str, str]] = []
        for did in selected_docs:
            doc = self.get_document(did)
            if not doc:
                continue
            meta, text = doc['metadata'], doc['text']
            name = meta.get('name', did)
            if selected_spans and did in selected_spans:
                hmap = {h['id']: h for h in meta.get('highlights', [])}
                for hid in selected_spans[did]:
                    if hid in hmap:
                        h = hmap[hid]
                        snippet = text[h['start']:h['end']].strip()
                        if snippet:
                            msgs.append({"role": "system", "content": f"[Document: {name} - {h['label']}]\n\n{snippet}"})
            else:
                if text.strip():
                    msgs.append({"role": "system", "content": f"[Document: {name}]\n\n{text.strip()}"})
        return msgs

    def get_context_token_count(self) -> Dict[str, int]:
        selected = [m for m in self.list_documents() if m.get('selected')]
        total = 0
        per_doc: Dict[str, int] = {}
        for m in selected:
            per_doc[m['name']] = int(m.get('token_estimate_total', 0))
            total += per_doc[m['name']]
        return {"total_tokens": total, "doc_tokens": per_doc, "doc_count": len(selected)}


# Global instance
context_documents = ContextDocuments()

# --- Lightweight wrapper functions expected by Flask routes ---

def list_documents() -> List[Dict[str, Any]]:
    return context_documents.list_documents()

def get_document(doc_id_or_name: str) -> Optional[Dict[str, Any]]:
    # Try by id
    doc = context_documents.get_document(doc_id_or_name)
    if doc:
        return doc["metadata"]
    # Fallback by name
    for meta in context_documents.list_documents():
        if meta.get("name") == doc_id_or_name:
            return meta
    return None

def save_document(name: str, content: bytes | str):
    """Accept bytes (pdf/docx) or str (txt/md), write temp file, ingest, return (ok, meta)."""
    import tempfile, os
    suffix = os.path.splitext(name)[1].lower() or ".txt"
    binary = isinstance(content, (bytes, bytearray))
    mode = 'wb' if binary else 'w'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        if binary:
            tmp.write(content)
        else:
            tmp.write(content)
        tmp_path = tmp.name
    try:
        meta = context_documents.ingest_document(tmp_path, original_name=name)
        return True, meta
    except Exception as e:
        print(f"save_document error: {e}")
        return False, {"error": str(e)}
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass

def delete_document(doc_id_or_name: str) -> bool:
    # Try direct id
    if context_documents.delete_document(doc_id_or_name):
        return True
    # Fallback by name
    for meta in context_documents.list_documents():
        if meta.get("name") == doc_id_or_name:
            return context_documents.delete_document(meta["doc_id"])
    return False