# ingestion/scraper.py
from __future__ import annotations
import asyncio
import ssl
import httpx
from bs4 import BeautifulSoup

_SEMAPHORE = asyncio.Semaphore(20)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def extract_text_from_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines) if lines else ""


def build_document_text(meta: dict, syllabus_text: str | None, skill_bridge: str) -> str:
    header = (
        f"課程代號: {meta['course_id']}\n"
        f"課程名稱: {meta['name']}\n"
        f"開課系所: {meta['department']}\n"
        f"授課教師: {meta['teacher']}\n"
        f"學分: {meta['credits']}\n"
        f"修別: {meta['kind']}\n\n"
    )
    body = syllabus_text or f"[無完整課綱，以課程名稱推估] 課程名稱：{meta['name']}"
    bridge = f"\n\n=== 課程技能對應（自動生成）===\n課程代號: {meta['course_id']}\n{skill_bridge}"
    return header + body + bridge


async def fetch_syllabus_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch syllabus HTML and extract text. Returns None after 3 failed attempts."""
    for attempt in range(3):
        try:
            async with _SEMAPHORE:
                resp = await client.get(url, timeout=15.0)
                resp.raise_for_status()
                return extract_text_from_html(resp.text)
        except Exception:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    return None


def make_httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=False,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qrysub.nccu.edu.tw/"},
        follow_redirects=True,
    )
