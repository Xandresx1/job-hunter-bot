"""Buscojobs (global + Perú) - scraping HTML con plantilla genérica."""
from __future__ import annotations

import re
from typing import Any

from core.models import JobOffer, normalize_text
from scrapers.base import BaseScraper, ScraperError


class BuscojobsScraper(BaseScraper):
    """Fuente NIVEL B (degradada): el WAF de Buscojobs responde 405 a muchas IPs.

    El scraper intenta los dominios configurados y, si todos bloquean, lanza
    ScraperError para que el circuit breaker la desactive temporalmente. Sus
    ofertas se cubren vía Jooble/JSearch.
    """

    name = "buscojobs"
    label = "Buscojobs"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Intenta el buscador en cada dominio configurado."""
        base_urls = [
            str(u).rstrip("/")
            for u in (self.option("base_urls", ["https://www.buscojobs.com"]) or [])
        ]
        offers: list[JobOffer] = []
        errors: list[str] = []
        for base_url in base_urls:
            for keyword in keywords:
                for path in ("/buscar", "/empleos"):
                    url = f"{base_url}{path}"
                    try:
                        soup = self.soup(url, params={"q": keyword})
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{url}: {exc}")
                        continue
                    found = self._parse(soup, base_url, keyword)
                    offers.extend(found)
                    if found:
                        break
                if len(offers) >= self.max_offers:
                    break
        if not offers:
            raise ScraperError(
                "sin resultados ("
                + "; ".join(errors[:2] or ["HTML sin ofertas reconocibles"])
                + ")"
            )
        return offers

    def _parse(self, soup: Any, base_url: str, keyword: str) -> list[JobOffer]:
        """Extrae ofertas usando JSON-LD si existe, si no enlaces de oferta."""
        results: list[JobOffer] = []
        for block in self.http.json_ld_blocks(soup):
            if str(block.get("@type", "")).lower() == "jobposting":
                offer = self.offer_from_json_ld(block, base_url)
                if offer and offer.title:
                    results.append(offer)
        if results:
            return results

        needle = normalize_text(keyword)
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not re.search(r"/(empleo|oferta|trabajo|job)s?/", href):
                continue
            title = anchor.get_text(" ", strip=True)
            if not title or len(title) < 5:
                continue
            if needle and needle.split()[0] not in normalize_text(title):
                continue
            url = href if href.startswith("http") else base_url + href
            results.append(
                self.make_offer(
                    title=title,
                    company="",
                    location="",
                    url=url,
                    description=title,
                )
            )
            if len(results) >= self.max_offers:
                break
        return results
