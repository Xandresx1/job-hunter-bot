"""Kitempleo Perú - buscador HTML (https://www.kitempleo.pe/search/?keywords=...)."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class KitempleoScraper(BaseScraper):
    """Fuente NIVEL B: cards con título, fecha, empresa y ubicación."""

    name = "kitempleo"
    label = "Kitempleo"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en el buscador del portal."""
        base_url = str(self.option("base_url", "https://www.kitempleo.pe")).rstrip("/")
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            try:
                soup = self.http.get_soup(f"{base_url}/search/", params={"keywords": keyword})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for anchor in soup.select('a[href*="/empleo/"]'):
                heading = anchor.select_one("h3") or anchor.select_one("h4")
                title = heading.get_text(strip=True) if heading else anchor.get_text(" ", strip=True)
                if not title:
                    continue
                href = anchor.get("href", "")
                url = href if href.startswith("http") else base_url + href
                attribs = anchor.select_one("div.blog-three-attrib")
                date_text, company, location = "", "", ""
                if attribs:
                    parts = [
                        div.get_text(" ", strip=True)
                        for div in attribs.select("div")
                        if div.get_text(strip=True)
                    ]
                    if len(parts) >= 1:
                        date_text = parts[0]
                    if len(parts) >= 2:
                        company = parts[1]
                    if len(parts) >= 3:
                        location = parts[2]
                offers.append(
                    self.make_offer(
                        title=title,
                        company=company,
                        location=location or "Perú",
                        url=url,
                        description=anchor.get_text(" ", strip=True),
                        posted_at=self.parse_datetime(date_text),
                    )
                )
                if len(offers) >= self.max_offers:
                    break
        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers
