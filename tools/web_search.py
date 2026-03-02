"""
Web Search Tool for ThreadBear

Search the web via DuckDuckGo (no API key) and scrape result pages.
"""
import re
import time
import logging
from typing import List, Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .registry import tool_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MAX_RESULTS = 10          # DuckDuckGo results to fetch
MAX_SCRAPE = 3            # Pages to actually scrape
SCRAPE_TIMEOUT = 10       # Per-page timeout (seconds)
MAX_CONTENT_PER_PAGE = 6000  # Characters per scraped page (after relevance filtering)
RATE_LIMIT_SECONDS = 1.0  # Min delay between requests to same domain
_last_request_times: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# robots.txt checking
# ---------------------------------------------------------------------------
def _can_fetch(url: str) -> bool:
    """Check robots.txt for generic user agent. Returns True if allowed or on error."""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return True  # Assume allowed if robots.txt can't be read


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def _respect_rate_limit(url: str):
    """Enforce per-domain rate limiting."""
    domain = urlparse(url).netloc
    now = time.time()
    if domain in _last_request_times:
        elapsed = now - _last_request_times[domain]
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_times[domain] = time.time()


# ---------------------------------------------------------------------------
# Query-aware relevance scoring
# ---------------------------------------------------------------------------
_STOP_WORDS = {
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'between',
    'through', 'during', 'before', 'after', 'and', 'but', 'or', 'nor',
    'not', 'so', 'yet', 'both', 'either', 'neither', 'each', 'every',
    'this', 'that', 'these', 'those', 'it', 'its', 'i', 'me', 'my',
    'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'what',
    'which', 'who', 'whom', 'how', 'when', 'where', 'why',
}


def _query_terms(query: str) -> List[str]:
    """Extract meaningful lowercase terms from a query, removing stop words."""
    words = re.findall(r'[a-z0-9]+', query.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _score_block(text: str, terms: List[str]) -> float:
    """Score a text block by how many query terms it contains, with bonus for density."""
    if not terms or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for t in terms if t in text_lower)
    if hits == 0:
        return 0.0
    # Base score: fraction of query terms matched
    coverage = hits / len(terms)
    # Density bonus: shorter blocks with the same hits score higher
    density = hits / max(len(text.split()), 1)
    return coverage + (density * 0.5)


# ---------------------------------------------------------------------------
# Content extraction (BeautifulSoup) — query-aware
# ---------------------------------------------------------------------------
def _extract_content(html: str, url: str, query: str = "") -> Dict:
    """
    Extract content from HTML, filtered by relevance to the search query.

    Strategy:
    1. Remove non-content elements (script, style, nav, footer, header, aside)
    2. Find main content container
    3. Extract structured blocks: headings, paragraphs, list items, table rows
    4. Score each block against query terms
    5. Keep intro context + top-scoring blocks within budget
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Find main content container
    main = (
        soup.find('main') or
        soup.find('article') or
        soup.find('div', class_=re.compile(r'(?:^|\s|-)(?:content|article|post|entry)(?:\s|-|$)', re.I)) or
        soup.find('div', id=re.compile(r'(?:^|-)(?:content|article|post|entry|main-content)(?:-|$)', re.I))
    )
    container = main if main else soup

    # Extract structured blocks with their type
    blocks = []
    seen_texts = set()
    for tag in container.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                    'p', 'li', 'tr', 'blockquote', 'pre']):
        text = tag.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text or len(text) < 10:
            continue
        # Deduplicate
        sig = text[:80]
        if sig in seen_texts:
            continue
        seen_texts.add(sig)

        tag_name = tag.name
        # Table rows: reconstruct as pipe-delimited for readability
        if tag_name == 'tr':
            cells = [c.get_text(strip=True) for c in tag.find_all(['th', 'td'])]
            if cells:
                text = ' | '.join(cells)

        blocks.append({
            'text': text,
            'tag': tag_name,
            'is_heading': tag_name.startswith('h'),
        })

    # Fallback: if structured extraction got very little, grab all text
    if sum(len(b['text']) for b in blocks) < 200:
        fallback = container.get_text(separator=' ', strip=True)
        fallback = re.sub(r'\s+', ' ', fallback).strip()
        if fallback:
            return {
                'url': url,
                'title': title,
                'content': fallback[:MAX_CONTENT_PER_PAGE],
                'truncated': len(fallback) > MAX_CONTENT_PER_PAGE,
            }

    # If no query, just concatenate blocks up to budget (old behavior)
    terms = _query_terms(query) if query else []
    if not terms:
        text = '\n'.join(b['text'] for b in blocks)
        return {
            'url': url,
            'title': title,
            'content': text[:MAX_CONTENT_PER_PAGE],
            'truncated': len(text) > MAX_CONTENT_PER_PAGE,
        }

    # --- Query-aware filtering ---

    # Score each block
    for b in blocks:
        b['score'] = _score_block(b['text'], terms)
        # Headings get a bonus — they provide structure even if low-scoring
        if b['is_heading']:
            b['score'] += 0.3

    # Always keep: first few blocks as intro context (up to 800 chars)
    intro_blocks = []
    intro_chars = 0
    for b in blocks:
        if intro_chars >= 800:
            break
        intro_blocks.append(b)
        intro_chars += len(b['text']) + 1

    # Sort remaining blocks by score, keep the best ones
    remaining = [b for b in blocks if b not in intro_blocks]
    remaining.sort(key=lambda b: b['score'], reverse=True)

    # Build output: intro + top-scoring blocks, maintaining original order
    selected = set(id(b) for b in intro_blocks)
    budget = MAX_CONTENT_PER_PAGE - intro_chars
    for b in remaining:
        if b['score'] <= 0 and budget < MAX_CONTENT_PER_PAGE * 0.3:
            continue  # Skip zero-score blocks unless we have lots of budget left
        if budget <= 0:
            break
        selected.add(id(b))
        budget -= len(b['text']) + 1

    # Reassemble in document order
    output_blocks = [b for b in blocks if id(b) in selected]
    text = '\n'.join(b['text'] for b in output_blocks)
    total_chars = sum(len(b['text']) for b in blocks)

    return {
        'url': url,
        'title': title,
        'content': text[:MAX_CONTENT_PER_PAGE],
        'truncated': total_chars > len(text),
    }


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------
def _scrape_page(url: str, query: str = "") -> Optional[Dict]:
    """
    Scrape a single page with robots.txt respect and rate limiting.
    Returns extracted content dict or None on failure.
    Query is used for relevance-aware content filtering.
    """
    if not _can_fetch(url):
        logger.info(f"robots.txt disallows: {url}")
        return None

    _respect_rate_limit(url)

    try:
        resp = requests.get(
            url,
            timeout=SCRAPE_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
        )
        resp.raise_for_status()

        # Only process HTML responses
        content_type = resp.headers.get('content-type', '')
        if 'html' not in content_type.lower() and 'text' not in content_type.lower():
            return {'url': url, 'title': '', 'content': f'[Non-HTML content: {content_type}]', 'truncated': False}

        return _extract_content(resp.text, url, query=query)

    except requests.RequestException as e:
        logger.warning(f"Failed to scrape {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo search
# ---------------------------------------------------------------------------
def _perform_search(query: str, time_range: Optional[str] = None,
                    max_results: int = MAX_RESULTS) -> List[Dict]:
    """
    Search DuckDuckGo and return results.

    Each result: {'title': str, 'body': str, 'href': str}

    time_range: 'd' (day), 'w' (week), 'm' (month), 'y' (year), or None
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    try:
        with DDGS() as ddgs:
            kwargs = {'max_results': max_results}
            if time_range and time_range in ('d', 'w', 'm', 'y'):
                kwargs['timelimit'] = time_range
            results = ddgs.text(query, **kwargs)
            if not isinstance(results, list):
                results = list(results)
            return results
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------
def web_search(args: dict) -> dict:
    """
    Search the web and return results with scraped content.

    Flow:
    1. Search DuckDuckGo for the query
    2. Scrape top N result pages
    3. Return search results + scraped content
    """
    query = args.get('query', '').strip()
    if not query:
        return {'error': 'No search query provided'}

    num_results = min(args.get('num_results', MAX_SCRAPE), MAX_SCRAPE)
    time_range = args.get('time_range')  # d/w/m/y or None
    scrape = args.get('scrape', True)    # Whether to scrape result pages

    # Step 1: Search
    search_results = _perform_search(query, time_range)

    if not search_results:
        return {
            'query': query,
            'results': [],
            'scraped': [],
            'message': 'No search results found. Try a different query.'
        }

    # Format search results
    formatted_results = []
    for i, r in enumerate(search_results):
        formatted_results.append({
            'rank': i + 1,
            'title': r.get('title', ''),
            'snippet': r.get('body', ''),
            'url': r.get('href', ''),
        })

    # Step 2: Scrape top results (if enabled) — query-aware filtering
    scraped = []
    if scrape:
        urls_to_scrape = [r['url'] for r in formatted_results[:num_results] if r['url']]
        for url in urls_to_scrape:
            try:
                page = _scrape_page(url, query=query)
                if page and page.get('content'):
                    scraped.append(page)
            except Exception as e:
                logger.warning(f"Scrape error for {url}: {e}")

    return {
        'query': query,
        'num_search_results': len(formatted_results),
        'results': formatted_results,
        'scraped': scraped,
        'num_scraped': len(scraped),
    }


# ---------------------------------------------------------------------------
# Register tool
# ---------------------------------------------------------------------------
tool_registry.register_tool('web_search', web_search, {
    'description': (
        'Search the web using DuckDuckGo and optionally scrape result pages for content. '
        'Returns search results (title, snippet, URL) and scraped page text. '
        'Use this when you need current information, facts, or research from the internet.'
    ),
    'properties': {
        'query': {
            'type': 'string',
            'description': 'The search query (2-10 words recommended for best results)'
        },
        'num_results': {
            'type': 'integer',
            'description': 'Number of result pages to scrape (1-3, default 3)'
        },
        'time_range': {
            'type': 'string',
            'description': 'Time filter: "d" (past day), "w" (week), "m" (month), "y" (year), or omit for no limit'
        },
        'scrape': {
            'type': 'boolean',
            'description': 'Whether to scrape result pages for full content (default true). Set false for just search snippets.'
        }
    },
    'required': ['query']
}, timeout_s=60)
