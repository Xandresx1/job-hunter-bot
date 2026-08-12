"""Jobrapido Perú - agregador con render JS (fuente degradada sin Playwright)."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class JobrapidoScraper(BaseScraper):
    """Fuente NIVEL B (degradada): Jobrapido renderiza los resultados con JS y
    aplica rate limit agresivo (HTTP 429). Con `use_playwright: true` funciona;
    sin él suele devolver el HTML sin ofertas y la fuente se desactiva sola.
    """

    name = "jobrapido"
    label = "Jobrapido Perú"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta una keyword por ciclo (rate limit) y parsea los resultados."""
        base_url = str(self.option("base_url", "https://pe.jobrapido.com")).rstrip("/")
        max_keywords = int(self.option("max_keywords_per_cycle", 1))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords[:max_keywords]:
            try:
                soup = self.soup(
                    f"{base_url}/", params={"w": keyword}, wait_selector="div.result-list"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for block in self.http.json_ld_blocks(soup):
                if str(block.get("@type", "")).lower() == "jobposting":
                    offer = self.offer_from_json_ld(block, base_url)
                    if offer and offer.title:
                        offer.source = self.name
                        offers.append(offer)
            for item in soup.select("div.result-list li, li.result, div.result"):
                link = item.select_one("a[href]")
                title_tag = item.select_one("h2, h3, span.title")
                if not link or not title_tag:
                    continue
                href = link.get("href", "")
                company_tag = item.select_one("span.company, div.company")
                location_tag = item.select_one("span.location, div.location")
                date_tag = item.select_one("span.date, time")
                offers.append(
                    self.make_offer(
                        title=title_tag.get_text(" ", strip=True),
                        company=company_tag.get_text(" ", strip=True) if company_tag else "",
                        location=location_tag.get_text(" ", strip=True)
                        if location_tag
                        else "Perú",
                        url=href if href.startswith("http") else base_url + href,
                        description=item.get_text(" ", strip=True),
                        posted_at=self.parse_datetime(
                            date_tag.get_text(" ", strip=True) if date_tag else ""
                        ),
                    )
                )
        if not offers:
            raise ScraperError(
                "sin resultados; requiere JS/Playwright o hubo rate limit ("
                + "; ".join(errors[:2] or ["HTML sin ofertas"])
                + ")"
            )
        return offers[: self.max_offers]
