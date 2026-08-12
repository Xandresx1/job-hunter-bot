"""Teamtailor (Enaex Perú) - listado HTML + JSON-LD schema.org/JobPosting."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class TeamtailorScraper(BaseScraper):
    """Fuente NIVEL A/B: Teamtailor incluye JSON-LD JobPosting en cada vacante."""

    name = "teamtailor_enaex"
    label = "Teamtailor (Enaex Perú)"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Lee el listado de vacantes y luego el JSON-LD de cada detalle."""
        base_url = str(self.option("base_url", "https://enaexperu.teamtailor.com")).rstrip("/")
        max_details = int(self.option("max_details", 15))
        list_url = f"{base_url}/jobs"
        try:
            soup = self.http.get_soup(list_url)
        except Exception as exc:  # noqa: BLE001
            raise ScraperError(str(exc)) from exc

        links: list[str] = []
        for anchor in soup.select('a[href*="/jobs/"]'):
            href = anchor.get("href", "").split("?")[0]
            if not href or href.rstrip("/").endswith("/jobs"):
                continue
            url = href if href.startswith("http") else base_url + href
            if url not in links:
                links.append(url)

        offers: list[JobOffer] = []
        errors: list[str] = []
        for url in links[:max_details]:
            try:
                detail = self.http.get_soup(url)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                continue
            block = self.http.find_job_posting(detail)
            if block:
                offer = self.offer_from_json_ld(block, url)
            else:
                heading = detail.select_one("h1")
                body = detail.select_one("div.prose, main")
                offer = self.make_offer(
                    title=heading.get_text(strip=True) if heading else "",
                    company="Enaex Perú",
                    location="Perú",
                    url=url,
                    description=body.get_text(" ", strip=True) if body else "",
                )
            if offer and offer.title:
                if not offer.company:
                    offer.company = "Enaex Perú"
                offer.source = self.name
                offers.append(offer)
        if not offers and errors:
            raise ScraperError(errors[0])
        return offers
