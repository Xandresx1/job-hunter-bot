"""Bumeran Perú - endpoint interno JSON con fallback a HTML/Playwright."""
from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class BumeranScraper(BaseScraper):
    """Fuente NIVEL B/C (degradada): Bumeran está detrás de Cloudflare.

    Orden de intentos:
      1. Endpoint interno JSON `/api/candidatos/search-jobs`.
      2. HTML de búsqueda (JSON-LD si existe).
      3. Playwright (si `advanced.use_playwright: true`).
    """

    name = "bumeran"
    label = "Bumeran Perú"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Intenta API interna y luego HTML por cada keyword."""
        base_url = str(self.option("base_url", "https://www.bumeran.com.pe")).rstrip("/")
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            api_offers, api_error = self._try_api(base_url, keyword)
            if api_offers:
                offers.extend(api_offers)
                continue
            if api_error:
                errors.append(api_error)
            html_offers, html_error = self._try_html(base_url, keyword)
            offers.extend(html_offers)
            if html_error:
                errors.append(html_error)
            if len(offers) >= self.max_offers:
                break
        if not offers:
            raise ScraperError(
                "bloqueado por Cloudflare o HTML sin datos ("
                + "; ".join(errors[:2] or ["sin detalle"])
                + ")"
            )
        return offers

    # ------------------------------------------------------------------- API
    def _try_api(self, base_url: str, keyword: str) -> tuple[list[JobOffer], str]:
        """Endpoint interno JSON de Bumeran (puede requerir cabeceras de sitio)."""
        url = f"{base_url}/api/candidatos/search-jobs"
        payload = {
            "filters": [],
            "query": keyword,
            "page": 0,
            "pageSize": 20,
            "sort": "RECIENTES",
        }
        try:
            response = self.http.post(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-site-id": self.option("site_id", "BMPE"),
                    "Origin": base_url,
                    "Referer": f"{base_url}/empleos.html",
                },
            )
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return [], f"api {keyword}: {exc}"

        items = data.get("content") or data.get("data") or data.get("avisos") or []
        offers: list[JobOffer] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            detail_id = item.get("id") or item.get("idAviso") or ""
            slug = item.get("seoDenominacion") or item.get("titulo") or ""
            offers.append(
                self.make_offer(
                    title=item.get("titulo") or item.get("title", ""),
                    company=(item.get("empresa") or {}).get("denominacion", "")
                    if isinstance(item.get("empresa"), dict)
                    else str(item.get("empresa") or ""),
                    location=item.get("localizacion") or item.get("ciudad", "") or "Perú",
                    salary=str(item.get("salario") or ""),
                    url=f"{base_url}/empleos/{slug}-{detail_id}.html"
                    if detail_id
                    else f"{base_url}/empleos.html",
                    description=item.get("detalle") or item.get("descripcion", ""),
                    posted_at=self.parse_datetime(
                        item.get("fechaPublicacion") or item.get("fecha")
                    ),
                )
            )
        return offers, ""

    # ------------------------------------------------------------------ HTML
    def _try_html(self, base_url: str, keyword: str) -> tuple[list[JobOffer], str]:
        """Página de búsqueda: intenta JSON-LD y luego tarjetas HTML."""
        slug = keyword.strip().lower().replace(" ", "-")
        url = f"{base_url}/empleos-busqueda-{slug}.html"
        try:
            soup: BeautifulSoup = self.soup(url, wait_selector="a[href*='/empleos/']")
        except Exception as exc:  # noqa: BLE001
            return [], f"html {keyword}: {exc}"

        offers: list[JobOffer] = []
        for block in self.http.json_ld_blocks(soup):
            if str(block.get("@type", "")).lower() == "jobposting":
                offer = self.offer_from_json_ld(block, url)
                if offer and offer.title:
                    offers.append(offer)
        if offers:
            return offers, ""

        for anchor in soup.select('a[href*="/empleos/"]'):
            title = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "")
            if not title or len(title) < 6 or not href:
                continue
            offers.append(
                self.make_offer(
                    title=title,
                    company="",
                    location="Perú",
                    url=href if href.startswith("http") else base_url + href,
                    description=title,
                )
            )
        return offers, "" if offers else f"html {keyword}: sin tarjetas"
