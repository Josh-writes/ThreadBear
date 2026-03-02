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
MAX_CONTENT_PER_PAGE = 3000  # Characters per scraped page
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
# Content extraction (BeautifulSoup)
# ---------------------------------------------------------------------------
def _extract_content(html: str, url: str) -> Dict:
    """
    Extract clean text content from HTML.

    Strategy:
    1. Remove non-content elements (script, style, nav, footer, header, aside)
    2. Find main content container (main, article, content div)
    3. Extract paragraphs; fall back to all text
    4. Clean whitespace
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Try to find main content container
    # Use word-boundary patterns to avoid matching e.g. 'header-main__container'
    main = (
        soup.find('main') or
        soup.find('article') or
        soup.find('div', class_=re.compile(r'(?:^|\s|-)(?:content|article|post|entry)(?:\s|-|$)', re.I)) or
        soup.find('div', id=re.compile(r'(?:^|-)(?:content|article|post|entry|main-content)(?:-|$)', re.I))
    )

    # Try container first, fall back to whole soup if container has little text
    container = main if main else soup
    paragraphs = container.find_all('p')
    text = ' '.join(p.get_text().strip() for p in paragraphs if p.get_text().strip())

    # If container gave us very little, try the whole document
    if len(text) < 100 and container is not soup:
        paragraphs = soup.find_all('p')
        text = ' '.join(p.get_text().strip() for p in paragraphs if p.get_text().strip())

    # Fallback: all text
    if len(text) < 100:
        text = soup.get_text(separator=' ', strip=True)

    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Truncate
    truncated = len(text) > MAX_CONTENT_PER_PAGE
    text = text[:MAX_CONTENT_PER_PAGE]

    return {
        'url': url,
        'title': title,
        'content': text,
        'truncated': truncated,
    }


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------
def _scrape_page(url: str) -> Optional[Dict]:
    """
    Scrape a single page with robots.txt respect and rate limiting.
    Returns extracted content dict or None on failure.
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

        return _extract_content(resp.text, url)

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

    # Step 2: Scrape top results (if enabled)
    scraped = []
    if scrape:
        urls_to_scrape = [r['url'] for r in formatted_results[:num_results] if r['url']]
        for url in urls_to_scrape:
            try:
                page = _scrape_page(url)
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
