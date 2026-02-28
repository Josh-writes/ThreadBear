"""
URL Reader for ThreadBear

Handles web page ingestion using requests + BeautifulSoup.
Converts HTML to clean markdown text.
"""
from .registry import reader_registry


class UrlReader:
    """Reader for web URLs."""

    @staticmethod
    def extract_text(url):
        """Fetch URL, convert to clean text."""
        # Try trafilatura first (best for article extraction)
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded, include_links=True)
                if text:
                    return text
        except ImportError:
            pass
        except Exception:
            pass
        
        # Fallback: requests + BeautifulSoup
        try:
            import requests
            from bs4 import BeautifulSoup
            
            resp = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ThreadBear/1.0; +https://github.com/threadbear)'
            })
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Remove scripts, styles, and other non-content elements
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            
            # Get text
            text = soup.get_text(separator='\n', strip=True)
            
            # Clean up whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            return '\n'.join(lines)
            
        except ImportError:
            raise ImportError("URL reading requires requests and beautifulsoup4: pip install requests beautifulsoup4")
        except Exception as e:
            raise Exception(f"Failed to fetch URL: {e}")

    @staticmethod
    def extract_segments(url):
        """Section-based segmentation from HTML headings."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("URL reading requires requests and beautifulsoup4: pip install requests beautifulsoup4")
        
        resp = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ThreadBear/1.0)'
        })
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Remove non-content elements
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        
        # Find all headings
        headings = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        
        segments = []
        for i, heading in enumerate(headings):
            # Get content between this heading and the next
            content = []
            current = heading.next_sibling
            while current and current.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                if hasattr(current, 'get_text'):
                    content.append(current.get_text(strip=True))
                elif isinstance(current, str) and current.strip():
                    content.append(current.strip())
                current = current.next_sibling if hasattr(current, 'next_sibling') else None
            
            text = heading.get_text(strip=True)
            if content:
                text += '\n\n' + '\n'.join(content)
            
            if text.strip():
                segments.append({
                    'text': text,
                    'start': i,
                    'end': i + 1,
                    'tokens': len(text) // 4,
                    'label': heading.get_text(strip=True)[:60]
                })
        
        # If no headings found, return entire content as one segment
        if not segments:
            text = soup.get_text(separator='\n', strip=True)
            if text.strip():
                segments.append({
                    'text': text,
                    'start': 0,
                    'end': 1,
                    'tokens': len(text) // 4,
                    'label': 'Web Page Content'
                })
        
        return segments


# URL reader is registered separately (not by extension)
# It's used for URL ingestion, not file uploads
