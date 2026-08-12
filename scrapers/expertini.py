"""Expertini Perú - scraping HTML con JSON-LD (fuente degradada si bloquea)."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, strip_accents
from scrapers.base import BaseScraper, ScraperError


class ExpertiniScraper(BaseScraper):
    """Fuente NIVEL B (degradada): Expertini responde 403 a muchas IPs de VPS."""

    name = "expertini"
    label = "Expertini Perú"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en el buscador público."""
        base_url = str(self.option("base_url", "https://pe.expertini.com")).rstrip("/")
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            slug = strip_accents(keyword).lower().replace(" ", "-")
            for url in (
                f"{base_url}/jobs/search/{slug}/",
                f"{base_url}/search/?q={keyword.replace(' ', '+')}",
            ):
                try:
                    soup = self.soup(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{url}: {exc}")
                    continue
                for block in self.http.json_ld_blocks(soup):
                    if str(block.get("@type", "")).lower() == "jobposting":
                        offer = self.offer_from_json_ld(block, url)
                        if offer and offer.title:
                            offer.source = self.name
                            offers.append(offer)
                for anchor in soup.select('a[href*="/view/"], a[href*="/job/"]'):
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
                if offers:
                    break
        if not offers:
            raise ScraperError(
                "bloqueado o sin resultados (" + "; ".join(errors[:2] or ["403"]) + ")"
            )
        return offers[: self.max_offers]
