"""Arbeitnow API (https://www.arbeitnow.com/api/job-board-api) - empleos en Europa."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, matches_any_keyword
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowScraper(BaseScraper):
    """Fuente NIVEL A: feed paginado sin key (Alemania, Finlandia, resto de Europa)."""

    name = "arbeitnow"
    label = "Arbeitnow"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Descarga hasta 3 páginas del feed y filtra por keywords."""
        offers: list[JobOffer] = []
        pages = int(self.option("pages", 3))
        errors: list[str] = []
        for page in range(1, pages + 1):
            try:
                data = self.http.get_json(API_URL, params={"page": page})
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                break
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                title = item.get("title", "")
                tags = " ".join(item.get("tags") or []) + " " + " ".join(item.get("job_types") or [])
                haystack = f"{title} {tags} {item.get('description', '')[:400]}"
                if keywords and not matches_any_keyword(haystack, keywords):
                    continue
                remote = bool(item.get("remote"))
                offers.append(
                    self.make_offer(
                        title=title,
                        company=item.get("company_name", ""),
                        location=item.get("location") or ("Remoto" if remote else ""),
                        url=item.get("url", ""),
                        description=item.get("description", ""),
                        is_remote=remote,
                        posted_at=self.parse_datetime(item.get("created_at")),
                    )
                )
            if len(offers) >= self.max_offers:
                break
        if not offers and errors:
            raise ScraperError(errors[0])
        return offers
