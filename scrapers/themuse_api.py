"""The Muse API (https://www.themuse.com/api/public/jobs) - sin API key."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, matches_any_keyword
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://www.themuse.com/api/public/jobs"


class TheMuseScraper(BaseScraper):
    """Fuente NIVEL A: filtra por nivel 'Entry Level' / 'Internship'."""

    name = "themuse"
    label = "The Muse"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Recorre las páginas de niveles junior y filtra por keywords."""
        levels = self.option("levels", ["Entry Level", "Internship"])
        pages = int(self.option("pages", 2))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for level in levels:
            for page in range(1, pages + 1):
                params = {"page": page, "level": level, "category": "Software Engineering"}
                try:
                    data = self.http.get_json(API_URL, params=params)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    break
                results = data.get("results") or []
                if not results:
                    break
                for item in results:
                    title = item.get("name", "")
                    contents = item.get("contents", "") or ""
                    haystack = f"{title} {contents[:400]}"
                    if keywords and not matches_any_keyword(haystack, keywords):
                        continue
                    location = ", ".join(
                        loc.get("name", "") for loc in (item.get("locations") or [])
                    )
                    company = (item.get("company") or {}).get("name", "")
                    url = (item.get("refs") or {}).get("landing_page", "")
                    offers.append(
                        self.make_offer(
                            title=title,
                            company=company,
                            location=location,
                            url=url,
                            description=contents,
                            posted_at=self.parse_datetime(item.get("publication_date")),
                        )
                    )
                if len(offers) >= self.max_offers:
                    break
        if not offers and errors:
            raise ScraperError(errors[0])
        return offers
