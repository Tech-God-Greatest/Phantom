"""
tools/web_tools.py — Real-time web search and web page reading.

TOOL CONTRACT (every tool file must follow this)
================================================
  ALL_TOOLS   list   → OpenAI tool schemas for the tools in this file
  execute     func   → execute(name: str, args: dict) -> str
"""

import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup
from utils.logger import log_error

# Try the fastest available DuckDuckGo library
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Tool implementations ──────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 3) -> str:
    """Search the web using DDGS with an HTML fallback."""
    results = []

    # 1. DDGS library (preferred)
    if DDGS is not None:
        try:
            ddg = DDGS()
            for item in list(ddg.text(query, max_results=max_results)):
                title   = item.get("title", "").strip()
                link    = item.get("href",  "").strip()
                snippet = item.get("body",  "").strip()
                if len(snippet) > 220:
                    snippet = snippet[:220] + "..."
                if title or link:
                    results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
        except Exception as e:
            log_error("WebSearch", f"DDGS Error: {e}")

    # 2. HTML fallback when DDGS returns nothing
    if not results:
        try:
            url  = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for result in soup.find_all("div", class_="result"):
                    title_tag   = result.find("a", class_="result__a")
                    snippet_tag = result.find("a", class_="result__snippet")
                    if not title_tag:
                        continue
                    title   = title_tag.get_text(strip=True)
                    snippet = snippet_tag.get_text(strip=True) if snippet_tag else "No snippet."
                    link    = title_tag.get("href", "")
                    if "/l/?" in link and "uddg=" in link:
                        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                        link   = parsed.get("uddg", [link])[0]
                    elif link.startswith("//"):
                        link = "https:" + link
                    results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
                    if len(results) >= max_results:
                        break
        except Exception as e:
            log_error("WebSearch", f"HTML Fallback Error: {e}")

    # 3. Always include system time for time/date queries
    now_str     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (%A)")
    query_lower = query.lower()
    if any(k in query_lower for k in ["time", "date", "clock", "today"]):
        time_info = f"System Current Time: {now_str}\n"
        if results:
            return time_info + "\n---\n" + "\n---\n".join(results)
        return time_info

    if not results:
        return f"No web search results found. (Current System Time: {now_str})"

    return "\n---\n".join(results)


def fetch_web_page(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return clean body text."""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > max_chars:
            text = text[:max_chars] + "... [content truncated]"
        return text or "Web page fetched but contained no readable text."
    except Exception as e:
        return f"Error fetching web page '{url}': {e}"


# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

ALL_TOOLS = [
    {
        "type": "function",
        "name": "web_search",
        "description": (
            "Search the internet for real-time information, news, current events, "
            "documentation, or facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query keywords."
                }
            },
            "required": ["query"]
        }
    },
    {
        "type": "function",
        "name": "fetch_web_page",
        "description": "Fetch and extract plain text content from a web page URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The HTTP/HTTPS URL of the page to read."
                }
            },
            "required": ["url"]
        }
    }
]


# ── Dispatcher (called by registry.dispatch) ──────────────────────────────────

def execute(name: str, args: dict) -> str:
    """Route a web tool call by name."""
    if name == "web_search":
        return web_search(args.get("query", ""))
    elif name == "fetch_web_page":
        return fetch_web_page(args.get("url", ""))
    else:
        return f"[web_tools] Unknown tool: '{name}'"


# ── Backwards-compat aliases ──────────────────────────────────────────────────
WEB_TOOLS        = ALL_TOOLS
execute_web_tool = execute
