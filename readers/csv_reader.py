"""
CSV Reader for ThreadBear

Handles .csv, .tsv files with row-based segmentation.
Converts to markdown tables for better LLM consumption.
"""
import csv
from pathlib import Path
from .registry import reader_registry
from .encoding import normalize_encoding


class CsvReader:
    """Reader for CSV and TSV files."""

    @staticmethod
    def extract_text(path):
        """Read CSV, return as markdown table."""
        text = normalize_encoding(path)
        lines = text.strip().split('\n')
        if not lines:
            return ''
        
        # Detect delimiter
        delimiter = '\t' if path.suffix.lower() == '.tsv' else ','
        
        # Parse and format as markdown table
        rows = []
        for line in lines:
            if delimiter == ',':
                # Handle quoted fields
                import io
                reader = csv.reader(io.StringIO(line))
                for row in reader:
                    rows.append(row)
            else:
                rows.append(line.split(delimiter))
        
        if not rows:
            return ''
        
        # Format as markdown table
        header = rows[0]
        markdown_lines = ['| ' + ' | '.join(str(cell) for cell in header) + ' |']
        markdown_lines.append('| ' + ' | '.join(['---'] * len(header)) + ' |')
        
        for row in rows[1:]:
            # Pad/truncate to match header length
            padded = row[:len(header)] + [''] * max(0, len(header) - len(row))
            markdown_lines.append('| ' + ' | '.join(str(cell) for cell in padded) + ' |')
        
        return '\n'.join(markdown_lines)

    @staticmethod
    def extract_segments(path, rows_per_segment=50):
        """Row-based segmentation with headers injected into every segment."""
        text = normalize_encoding(path)
        lines = text.strip().split('\n')
        
        delimiter = '\t' if path.suffix.lower() == '.tsv' else ','
        
        # Parse all rows
        rows = []
        for line in lines:
            if delimiter == ',':
                import io
                reader = csv.reader(io.StringIO(line))
                for row in reader:
                    rows.append(row)
            else:
                rows.append(line.split(delimiter))
        
        if len(rows) < 2:
            return [{
                'text': CsvReader.extract_text(path),
                'start': 0,
                'end': len(rows),
                'tokens': len(text) // 4,
                'label': 'All data'
            }]
        
        header = rows[0]
        data_rows = rows[1:]
        segments = []
        
        for i in range(0, len(data_rows), rows_per_segment):
            chunk = data_rows[i:i + rows_per_segment]
            # Rebuild as table with header
            markdown_lines = ['| ' + ' | '.join(str(cell) for cell in header) + ' |']
            markdown_lines.append('| ' + ' | '.join(['---'] * len(header)) + ' |')
            for row in chunk:
                padded = row[:len(header)] + [''] * max(0, len(header) - len(row))
                markdown_lines.append('| ' + ' | '.join(str(cell) for cell in padded) + ' |')
            
            segment_text = '\n'.join(markdown_lines)
            segments.append({
                'text': segment_text,
                'start': i + 1,
                'end': i + len(chunk) + 1,
                'tokens': len(segment_text) // 4,
                'label': f'Rows {i + 1}-{i + len(chunk)}'
            })
        
        return segments


reader_registry.register(['.csv', '.tsv'], CsvReader)
