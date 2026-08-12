"""Remotive API (https://remotive.com/api/remote-jobs) - empleos 100% remotos."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveScraper(BaseScraper):
    """Fuente NIVEL A: API pública sin autenticación."""

    name = "remotive"
    label = "Remotive"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta la API una vez por keyword."""
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            try:
                data = self.http.get_json(API_URL, params={"search": keyword, "limit": 50})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for item in data.get("jobs", [])[: self.max_offers]:
                offers.append(
                    self.make_offer(
                        title=item.get("title", ""),
                        company=item.get("company_name", ""),
                        location=item.get("candidate_required_location") or "Remoto",
                        salary=item.get("salary", "") or "",
                        url=item.get("url", ""),
                        description=item.get("description", ""),
                        is_remote=True,
                        posted_at=self.parse_datetime(item.get("publication_date")),
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
