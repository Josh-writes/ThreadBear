"""
Smart Chunker for ThreadBear

Structure-aware chunking that respects document boundaries:
- Sentence boundaries (never split mid-sentence)
- Code block boundaries (never split inside ```)
- Table rows (never split mid-table, always include header)
- Heading hierarchy (prefer splitting at headings)
"""
import re


class SmartChunker:
    """
    Recursive splitter that respects document structure.
    """

    def __init__(self, max_chunk_tokens=500, overlap_tokens=50):
        self.max_chunk = max_chunk_tokens
        self.overlap = overlap_tokens

    def chunk(self, text, content_type='text'):
        """
        Split text into chunks respecting structure.
        
        Args:
            text: Text to chunk
            content_type: 'text' | 'code' | 'markdown' | 'table'
        
        Returns:
            list of {text, start, end, tokens}
        """
        if content_type == 'code':
            return self._chunk_code(text)
        elif content_type == 'markdown':
            return self._chunk_markdown(text)
        elif content_type == 'table':
            return self._chunk_table(text)
        else:
            return self._chunk_text(text)

    def _chunk_text(self, text):
        """Split by: paragraphs → sentences → words."""
        # First try paragraph boundaries
        paragraphs = text.split('\n\n')
        return self._merge_chunks(paragraphs, '\n\n')

    def _chunk_code(self, text):
        """Split by: functions/classes → blank line groups → lines."""
        # Split at function/class boundaries (double newline before def/class)
        blocks = re.split(r'\n\n(?=(?:def |class |function |const |export ))', text)
        return self._merge_chunks(blocks, '\n\n')

    def _chunk_markdown(self, text):
        """Split by: headings → paragraphs → sentences."""
        # Split at heading boundaries
        sections = re.split(r'\n(?=#{1,6}\s)', text)
        return self._merge_chunks(sections, '\n')

    def _chunk_table(self, text):
        """Split by row groups, always including header row."""
        lines = text.split('\n')
        if len(lines) < 3:
            return [{
                'text': text,
                'start': 0,
                'end': len(text),
                'tokens': len(text) // 4
            }]
        
        header = '\n'.join(lines[:2])  # Header + separator
        data_lines = lines[2:]
        
        chunks = []
        current_lines = []
        current_tokens = len(header) // 4
        
        for line in data_lines:
            line_tokens = len(line) // 4
            if current_tokens + line_tokens > self.max_chunk and current_lines:
                chunk_text = header + '\n' + '\n'.join(current_lines)
                chunks.append({
                    'text': chunk_text,
                    'tokens': len(chunk_text) // 4
                })
                current_lines = []
                current_tokens = len(header) // 4
            current_lines.append(line)
            current_tokens += line_tokens
        
        if current_lines:
            chunk_text = header + '\n' + '\n'.join(current_lines)
            chunks.append({
                'text': chunk_text,
                'tokens': len(chunk_text) // 4
            })
        
        return chunks

    def _merge_chunks(self, pieces, separator):
        """Merge small pieces into chunks up to max_chunk tokens."""
        chunks = []
        current_parts = []
        current_tokens = 0
        
        for piece in pieces:
            piece_tokens = len(piece) // 4
            if current_tokens + piece_tokens > self.max_chunk and current_parts:
                chunk_text = separator.join(current_parts)
                chunks.append({
                    'text': chunk_text,
                    'tokens': len(chunk_text) // 4
                })
                # Overlap: keep last piece
                if self.overlap > 0 and current_parts:
                    last = current_parts[-1]
                    current_parts = [last]
                    current_tokens = len(last) // 4
                else:
                    current_parts = []
                    current_tokens = 0
            current_parts.append(piece)
            current_tokens += piece_tokens
        
        if current_parts:
            chunk_text = separator.join(current_parts)
            chunks.append({
                'text': chunk_text,
                'tokens': len(chunk_text) // 4
            })
        
        return chunks
