"""Get on Board API (https://www.getonbrd.com/api/v0) - empleos tech en LATAM."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://www.getonbrd.com/api/v0/search/jobs"
SITE = "https://www.getonbrd.com"


class GetOnBoardScraper(BaseScraper):
    """Fuente NIVEL A: JSON público (Chile, Perú, México, Argentina, remoto LATAM)."""

    name = "getonboard"
    label = "Get on Board"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en la API pública."""
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            try:
                data = self.http.get_json(
                    API_URL, params={"query": keyword, "per_page": 50, "expand": '["company"]'}
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for item in (data.get("data") or [])[: self.max_offers]:
                attrs = item.get("attributes") or {}
                company_data = attrs.get("company") or {}
                if isinstance(company_data, dict):
                    company = (
                        (company_data.get("data") or {}).get("attributes", {}).get("name")
                        or company_data.get("name")
                        or ""
                    )
                else:
                    company = str(company_data)
                cities = attrs.get("location_cities") or []
                countries = attrs.get("countries") or []
                remote = bool(attrs.get("remote"))
                location_parts = [
                    ", ".join(str(c) for c in cities if c),
                    ", ".join(str(c) for c in countries if c),
                ]
                location = " | ".join(p for p in location_parts if p)
                if remote:
                    zone = attrs.get("remote_zone") or "LATAM"
                    location = f"Remoto ({zone}) {location}".strip()
                min_salary = attrs.get("min_salary") or 0
                max_salary = attrs.get("max_salary") or 0
                salary = (
                    f"USD {int(min_salary):,} - {int(max_salary):,} / mes"
                    if min_salary and max_salary
                    else ""
                )
                slug = item.get("id") or attrs.get("slug") or ""
                url = attrs.get("url") or f"{SITE}/jobs/{slug}"
                description = " ".join(
                    str(attrs.get(field) or "")
                    for field in ("description", "functions", "desirable", "benefits")
                )
                offers.append(
                    self.make_offer(
                        title=attrs.get("title", ""),
                        company=company,
                        location=location or "LATAM",
                        salary=salary,
                        url=url,
                        description=description,
                        is_remote=remote,
                        posted_at=self.parse_datetime(attrs.get("published_at")),
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
