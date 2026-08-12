"""Jooble API (https://jooble.org/api/{key}) - requiere JOOBLE_API_KEY."""
from __future__ import annotations

import json
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_BASE = "https://jooble.org/api"


class JoobleScraper(BaseScraper):
    """Fuente NIVEL A: API REST oficial gratuita (POST con keywords + location)."""

    name = "jooble"
    label = "Jooble"
    tier = "A"
    requires_env = ("JOOBLE_API_KEY",)

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta cada combinación keyword x ubicación configurada."""
        self.ensure_credentials()
        api_key = self.env("JOOBLE_API_KEY")
        url = f"{API_BASE}/{api_key}"
        search_locations: list[str] = self.option(
            "locations",
            [locations.get("local_city", ""), locations.get("country", ""), "Remoto"],
        )
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            for location in [loc for loc in search_locations if loc]:
                payload = {"keywords": keyword, "location": location, "page": "1"}
                try:
                    response = self.http.post(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                    )
                    data = response.json()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{keyword}/{location}: {exc}")
                    continue
                for item in (data.get("jobs") or [])[: self.max_offers]:
                    offers.append(
                        self.make_offer(
                            title=item.get("title", ""),
                            company=item.get("company", ""),
                            location=item.get("location", "") or location,
                            salary=item.get("salary", "") or "",
                            url=item.get("link", ""),
                            description=item.get("snippet", ""),
                            posted_at=self.parse_datetime(item.get("updated")),
                        )
                    )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
