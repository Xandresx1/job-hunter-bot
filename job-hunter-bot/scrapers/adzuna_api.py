"""Adzuna API (https://developer.adzuna.com) - requiere ADZUNA_APP_ID/KEY."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_BASE = "https://api.adzuna.com/v1/api/jobs"


class AdzunaScraper(BaseScraper):
    """Fuente NIVEL A: cubre España, USA, México, Nueva Zelanda, Canadá, Alemania..."""

    name = "adzuna"
    label = "Adzuna"
    tier = "A"
    requires_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en los países configurados."""
        self.ensure_credentials()
        app_id = self.env("ADZUNA_APP_ID")
        app_key = self.env("ADZUNA_APP_KEY")
        countries: list[str] = self.option("countries", ["es", "us", "mx", "nz", "ca", "de"])
        per_page = int(self.option("results_per_page", 20))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for country in countries:
            for keyword in keywords:
                url = f"{API_BASE}/{country}/search/1"
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": keyword,
                    "results_per_page": per_page,
                    "content-type": "application/json",
                }
                try:
                    data = self.http.get_json(url, params=params)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{country}/{keyword}: {exc}")
                    continue
                for item in (data.get("results") or [])[: self.max_offers]:
                    location = (item.get("location") or {}).get("display_name", "")
                    company = (item.get("company") or {}).get("display_name", "")
                    salary_min = item.get("salary_min")
                    salary_max = item.get("salary_max")
                    salary = ""
                    if salary_min and salary_max:
                        salary = f"{int(salary_min):,} - {int(salary_max):,} ({country.upper()})"
                    offers.append(
                        self.make_offer(
                            title=item.get("title", ""),
                            company=company,
                            location=location,
                            salary=salary,
                            url=item.get("redirect_url", ""),
                            description=item.get("description", ""),
                            posted_at=self.parse_datetime(item.get("created")),
                        )
                    )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
