"""
Markdown Reader for ThreadBear

Handles .md, .markdown files with heading-based segmentation.
"""
import re
from pathlib import Path
from .registry import reader_registry
from .encoding import normalize_encoding


class MdReader:
    """Reader for Markdown files."""

    @staticmethod
    def extract_text(path):
        """Extract full markdown text."""
        return normalize_encoding(path)

    @staticmethod
    def extract_segments(path):
        """Segment by heading sections."""
        text = MdReader.extract_text(path)
        
        # Split by headings (# to ######)
        heading_pattern = r'^(#{1,6}\s+.+)$'
        lines = text.split('\n')
        
        segments = []
        current_segment = []
        current_heading = 'Introduction'
        segment_start = 0
        
        for i, line in enumerate(lines):
            match = re.match(heading_pattern, line)
            if match:
                # Save previous segment
                if current_segment:
                    segment_text = '\n'.join(current_segment)
                    segments.append({
                        'text': segment_text,
                        'start': segment_start,
                        'end': i,
                        'tokens': len(segment_text) // 4,
                        'label': current_heading
                    })
                # Start new segment
                current_heading = line.strip()
                current_segment = [line]
                segment_start = i
            else:
                current_segment.append(line)
        
        # Save last segment
        if current_segment:
            segment_text = '\n'.join(current_segment)
            segments.append({
                'text': segment_text,
                'start': segment_start,
                'end': len(lines),
                'tokens': len(segment_text) // 4,
                'label': current_heading
            })
        
        return segments


reader_registry.register(['.md', '.markdown'], MdReader)
