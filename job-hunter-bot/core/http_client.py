"""Cliente HTTP con User-Agent rotativo, delays por dominio y retries con backoff."""
from __future__ import annotations

import json
import random
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 10.0)


class HttpError(Exception):
    """Error HTTP no recuperable tras los reintentos."""


class HttpClient:
    """Sesión HTTP compartida por todos los scrapers."""

    def __init__(
        self,
        timeout: int = 20,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        max_retries: int = 3,
    ) -> None:
        self.timeout = timeout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max(1, max_retries)
        self.session = requests.Session()
        self.log = get_logger("http")
        self._last_request_at: dict[str, float] = {}

    # ------------------------------------------------------------------ utils
    def default_headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Headers de navegador real con UA rotativo."""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }
        if extra:
            headers.update(extra)
        return headers

    def _throttle(self, url: str) -> None:
        """Respeta un delay aleatorio entre requests al mismo dominio."""
        domain = urlparse(url).netloc.lower()
        if not domain:
            return
        last = self._last_request_at.get(domain)
        wait = random.uniform(self.min_delay, self.max_delay)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < wait:
                time.sleep(wait - elapsed)
        self._last_request_at[domain] = time.monotonic()

    # --------------------------------------------------------------- requests
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        throttle: bool = True,
        allowed_status: tuple[int, ...] = (200, 201, 202, 204),
        **kwargs: Any,
    ) -> requests.Response:
        """Ejecuta un request con retries y backoff exponencial (1s, 4s, 10s).

        Raises:
            HttpError: si tras los reintentos no se obtiene un status permitido.
        """
        last_error: str = ""
        for attempt in range(self.max_retries):
            if throttle:
                self._throttle(url)
            try:
                response = self.session.request(
                    method.upper(),
                    url,
                    headers=self.default_headers(headers),
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.log.debug("Intento %s/%s falló en %s (%s)", attempt + 1, self.max_retries, url, last_error)
            else:
                if response.status_code in allowed_status:
                    return response
                last_error = f"HTTP {response.status_code}"
                # 4xx que no son rate-limit no mejoran reintentando
                if response.status_code in (400, 401, 403, 404, 405, 410, 451, 999):
                    raise HttpError(f"{last_error} en {url}")
            if attempt < self.max_retries - 1:
                time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
        raise HttpError(f"{last_error or 'sin respuesta'} en {url}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """GET con retries."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        """POST con retries."""
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET que devuelve JSON."""
        response = self.get(url, headers={"Accept": "application/json, text/plain, */*"}, **kwargs)
        response.encoding = response.encoding or "utf-8"
        return response.json()

    def get_soup(self, url: str, parser: str = "html.parser", **kwargs: Any) -> BeautifulSoup:
        """GET que devuelve un BeautifulSoup."""
        response = self.get(url, **kwargs)
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return BeautifulSoup(response.text, parser)

    # -------------------------------------------------------------- JSON-LD
    @staticmethod
    def json_ld_blocks(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Devuelve todos los objetos JSON-LD embebidos en la página."""
        blocks: list[dict[str, Any]] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            raw = tag.string or tag.get_text() or ""
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    blocks.append(item)
                    for graph_item in item.get("@graph", []) or []:
                        if isinstance(graph_item, dict):
                            blocks.append(graph_item)
        return blocks

    @classmethod
    def find_job_posting(cls, soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        """Busca el bloque schema.org/JobPosting embebido (más estable que el HTML)."""
        for block in cls.json_ld_blocks(soup):
            types = block.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(str(t).lower() == "jobposting" for t in types if t):
                return block
        return None

    def fetch_description(self, url: str) -> str:
        """Descarga la página de detalle y devuelve la mejor descripción disponible.

        Prioriza el JSON-LD schema.org/JobPosting y, si no existe, toma el bloque de
        texto más largo de la página. Devuelve cadena vacía si falla.
        """
        try:
            soup = self.get_soup(url)
        except Exception as exc:  # noqa: BLE001 - el enriquecimiento es best-effort
            self.log.debug("No se pudo enriquecer %s: %s", url, exc)
            return ""
        block = self.find_job_posting(soup)
        if block and block.get("description"):
            return str(block["description"])
        candidates: list[str] = []
        for tag in soup.select("div, section, article, p"):
            text = tag.get_text(" ", strip=True)
            if 200 < len(text) < 12000:
                candidates.append(text)
        return max(candidates, key=len) if candidates else ""

    @classmethod
    def find_item_list(cls, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Devuelve los elementos de un schema.org/ItemList (listados de ofertas)."""
        for block in cls.json_ld_blocks(soup):
            if str(block.get("@type", "")).lower() == "itemlist":
                elements = block.get("itemListElement") or []
                return [e for e in elements if isinstance(e, dict)]
        return []


def playwright_get(url: str, timeout: int = 30, wait_selector: Optional[str] = None) -> str:
    """Descarga una página con Playwright headless + stealth (módulo opcional).

    Se importa de forma diferida: si Playwright no está instalado se lanza
    RuntimeError y el scraper degrada elegantemente.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            "Playwright no está instalado. Ejecuta: pip install playwright playwright-stealth "
            "&& playwright install chromium"
        ) from exc

    try:
        from playwright_stealth import stealth_sync  # type: ignore
    except ImportError:  # pragma: no cover
        stealth_sync = None  # type: ignore[assignment]

    user_agent = random.choice(USER_AGENTS)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=user_agent,
            locale="es-PE",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "es-PE,es;q=0.9,en;q=0.8"},
        )
        page = context.new_page()
        if stealth_sync is not None:
            try:
                stealth_sync(page)
            except Exception:  # noqa: BLE001 - stealth es best-effort
                pass
        try:
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            time.sleep(random.uniform(3.0, 8.0))
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
            return page.content()
        finally:
            context.close()
            browser.close()
