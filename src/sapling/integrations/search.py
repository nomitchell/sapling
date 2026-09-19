"""Literature, web search and public-source capture with retained provenance."""

import asyncio
import hashlib
import io
import ipaddress
import json
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader


class SearchUnavailable(RuntimeError):
    pass


class SourceRejected(ValueError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    summary: str
    provider: str
    source_id: str | None = None
    fallback_reason: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    open_access_url: str | None = None
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class SourceArtifact:
    id: str
    requested_url: str
    final_url: str
    title: str
    content_type: str
    retrieved_at: str
    sha256: str
    byte_size: int
    raw_path: str
    text_path: str
    metadata_path: str
    text: str
    links: list[dict[str, str]] = field(default_factory=list)


async def _resolve(hostname: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return list({record[4][0] for record in records})


async def validate_public_url(url: str, resolver: Callable[[str, int], Awaitable[list[str]]] = _resolve) -> str:
    """Reject local/private addresses on every hop; this is not a network sandbox.

    DNS is checked before retrieval. A network firewall remains necessary for
    protection from an adversary who controls DNS and attempts rebinding.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise SourceRejected("Sources must use a public HTTP or HTTPS URL.")
        if parts.username is not None or parts.password is not None:
            raise SourceRejected("Credentials in source URLs are not supported.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in {80, 443}:
            raise SourceRejected("Public source fetching is limited to ports 80 and 443.")
        hostname = parts.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            raise SourceRejected("Local and private network sources must be uploaded explicitly.")
        addresses = await resolver(hostname, port)
        if not addresses:
            raise SourceRejected("The source hostname did not resolve.")
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            if not ip.is_global or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped and not ip.ipv4_mapped.is_global):
                raise SourceRejected("Local and private network source addresses are blocked.")
    except (ValueError, OSError) as exc:
        if isinstance(exc, SourceRejected):
            raise
        raise SourceRejected("The source URL or hostname is invalid.") from exc
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def reconstruct_abstract(index: dict | None) -> str:
    positions: dict[int, str] = {}
    for word, offsets in (index or {}).items():
        for position in offsets:
            if isinstance(position, int) and 0 <= position < 30_000:
                positions[position] = word
    return " ".join(positions[position] for position in sorted(positions))


def extract_text(data: bytes, content_type: str) -> tuple[str, str]:
    if content_type == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "")[:200_000] for page in reader.pages[:300])[:2_000_000]
        title = str((reader.metadata or {}).get("/Title", ""))
        return title, text
    if content_type in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(data, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for element in soup(["script", "style", "noscript", "iframe", "svg"]):
            element.decompose()
        return title, soup.get_text("\n", strip=True)[:2_000_000]
    if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
        return "", data.decode("utf-8", errors="replace")[:2_000_000]
    raise SourceRejected(f"Unsupported source content type: {content_type or 'unknown'}")


class SearchClient:
    def __init__(
        self,
        searxng_url: str | None = None,
        openalex_api_key: str | None = None,
        *,
        tavily_api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
        max_source_bytes: int = 20_000_000,
        resolver: Callable[[str, int], Awaitable[list[str]]] = _resolve,
        native_fallback: bool = True,
        ddgs_factory: Callable | None = None,
    ) -> None:
        self.searxng_url = searxng_url.rstrip("/") if searxng_url else None
        self.openalex_api_key = openalex_api_key
        self.tavily_api_key = tavily_api_key
        self.timeout_seconds = timeout_seconds
        self.max_source_bytes = max_source_bytes
        self.resolver = resolver
        self.native_fallback = native_fallback
        self.ddgs_factory = ddgs_factory
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False, trust_env=False, headers={"User-Agent": "Sapling/0.1 (local research client)"})

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def __aenter__(self) -> "SearchClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    @staticmethod
    def _query(query: str, limit: int) -> tuple[str, int]:
        query = query.strip()
        if not query or len(query) > 2000:
            raise ValueError("Search queries must contain between 1 and 2000 characters.")
        return query, max(1, min(int(limit), 30))

    async def _json(self, url: str, **kwargs: object) -> dict:
        method = kwargs.pop("method", "GET")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.client.stream(method, url, follow_redirects=False, **kwargs) as response:
                    if response.status_code in {401, 403}:
                        raise SearchUnavailable("Search authorization failed. Check the provider key in Connections; for SearXNG, enable JSON in search.formats.")
                    if response.status_code == 429:
                        raise SearchUnavailable("The search provider rate limit was reached. Retry later or configure an API key.")
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 5_000_000:
                            raise SearchUnavailable("The search response exceeded the 5 MB limit.")
                        chunks.append(chunk)
                    result = json.loads(b"".join(chunks))
                    if not isinstance(result, dict):
                        raise SearchUnavailable("The search provider returned an unexpected response.")
                    return result
        except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            raise SearchUnavailable("Search could not complete. Check connectivity and the configured search service.") from exc

    async def search_literature(self, query: str, limit: int = 8) -> list[SearchResult]:
        query, limit = self._query(query, limit)
        headers = {"Authorization": f"Bearer {self.openalex_api_key}"} if self.openalex_api_key else {}
        payload = await self._json("https://api.openalex.org/works", params={"search": query, "per_page": limit}, headers=headers)
        results = []
        for work in payload.get("results", [])[:limit]:
            location = work.get("best_oa_location") or work.get("primary_location") or {}
            url = location.get("landing_page_url") or work.get("doi") or work.get("id", "")
            results.append(SearchResult(
                title=work.get("display_name") or "Untitled work", url=url,
                summary=reconstruct_abstract(work.get("abstract_inverted_index")), provider="openalex",
                source_id=work.get("id"), authors=[entry.get("author", {}).get("display_name", "") for entry in work.get("authorships", [])],
                year=work.get("publication_year"), doi=work.get("doi"),
                open_access_url=location.get("pdf_url") or (work.get("open_access") or {}).get("oa_url"),
            ))
        return results

    async def search_web(self, query: str, limit: int = 8) -> list[SearchResult]:
        query, limit = self._query(query, limit)
        if self.tavily_api_key:
            payload = await self._json(
                "https://api.tavily.com/search", method="POST",
                headers={"Authorization": f"Bearer {self.tavily_api_key}"},
                json={"query": query, "max_results": min(limit, 20), "search_depth": "basic",
                      "include_answer": False, "include_raw_content": False},
            )
            return [SearchResult(
                title=item.get("title", "Untitled result"), url=item["url"],
                summary=item.get("content", ""), provider="tavily",
            ) for item in payload.get("results", [])[:limit] if item.get("url")]
        failure = "SearXNG is not configured."
        if self.searxng_url:
            try:
                # Configured services may intentionally be localhost; model-
                # provided public source URLs are checked separately.
                payload = await self._json(f"{self.searxng_url}/search", params={"q": query, "format": "json", "categories": "general"})
                return [SearchResult(title=item.get("title", "Untitled result"), url=item.get("url", ""), summary=item.get("content", ""), provider="searxng") for item in payload.get("results", [])[:limit] if item.get("url")]
            except SearchUnavailable:
                failure = "The configured SearXNG service is unavailable."
        if not self.native_fallback:
            raise SearchUnavailable(f"{failure} Configure a working SearXNG URL or enable native web search.")
        return await self._search_native(query, limit, failure)

    async def _search_native(self, query: str, limit: int, reason: str) -> list[SearchResult]:
        factory = self.ddgs_factory
        if factory is None:
            try:
                from ddgs import DDGS
                factory = DDGS
            except ImportError as exc:
                raise SearchUnavailable("Native web search requires the ddgs package. Install project dependencies or start SearXNG.") from exc

        def search() -> list[dict]:
            # DDGS is a no-key metasearch client, not a guaranteed search API.
            # Keep certificate checks enabled and each upstream request bounded.
            with factory(timeout=max(1, min(int(self.timeout_seconds), 8)), verify=True) as engine:
                return engine.text(query, max_results=limit, backend="duckduckgo,bing,brave,mojeek")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                items = await asyncio.to_thread(search)
        except Exception as exc:
            raise SearchUnavailable("SearXNG and native DDGS web search are unavailable. Upstream search engines may be rate-limiting; retry later or start SearXNG.") from exc
        results = [SearchResult(title=str(item.get("title", "Untitled result"))[:1000], url=str(item.get("href", "")), summary=str(item.get("body", ""))[:10000], provider="ddgs", fallback_reason=reason) for item in items[:limit] if item.get("href")]
        if not results:
            raise SearchUnavailable("Native DDGS web search returned no results. Try a different query or start SearXNG.")
        return results

    async def fetch_source(self, url: str, destination_dir: str | Path) -> SourceArtifact:
        requested_url, current = url, url
        async with asyncio.timeout(self.timeout_seconds):
            for redirect in range(6):
                current = await validate_public_url(current, self.resolver)
                # Clear provider credentials/cookies. Never reuse authorization
                # from search requests when opening an arbitrary public source.
                async with self.client.stream("GET", current, follow_redirects=False, headers={"Accept": "text/html,application/pdf,text/plain;q=0.9", "Authorization": "", "Cookie": ""}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if redirect == 5 or not location:
                            raise SourceRejected("The source has too many redirects or an invalid redirect.")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > self.max_source_bytes:
                        raise SourceRejected("The source exceeds the download size limit.")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > self.max_source_bytes:
                            raise SourceRejected("The source exceeds the download size limit.")
                        data.extend(chunk)
                    break
            else:
                raise SourceRejected("The source redirect limit was exceeded.")
        raw = bytes(data)
        title, text = await asyncio.to_thread(extract_text, raw, content_type)
        links = []
        if content_type in {"text/html", "application/xhtml+xml"}:
            soup = BeautifulSoup(raw, "html.parser")
            seen = set()
            for anchor in soup.select("a[href]"):
                href = urljoin(current, anchor.get("href", ""))
                label = anchor.get_text(" ", strip=True)
                if (urlsplit(href).scheme in {"http", "https"} and href not in seen
                    and any(word in (label + " " + href).lower() for word in ("pdf", "html", "supplement", "full text", "code", "github"))):
                    links.append({"title": label[:160], "url": href})
                    seen.add(href)
                    if len(links) == 20:
                        break
        digest = hashlib.sha256(raw).hexdigest()
        destination = Path(destination_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        source_id = str(uuid4())
        raw_path = destination / f"{digest}.source"
        text_path = destination / f"{digest}.txt"
        metadata_path = destination / f"{source_id}.json"
        record = SourceArtifact(source_id, requested_url, current, title, content_type, datetime.now(timezone.utc).isoformat(), digest, len(raw), str(raw_path), str(text_path), str(metadata_path), text, links)
        raw_path.write_bytes(raw)
        text_path.write_text(text, encoding="utf-8")
        metadata = asdict(record)
        metadata.pop("text")
        metadata["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
