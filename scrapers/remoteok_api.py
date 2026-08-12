"""RemoteOK API (https://remoteok.com/api) - JSON público de empleos remotos."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, matches_any_keyword, normalize_text
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://remoteok.com/api"


class RemoteOkScraper(BaseScraper):
    """Fuente NIVEL A: un solo request devuelve el feed completo; se filtra local."""

    name = "remoteok"
    label = "RemoteOK"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Descarga el feed y filtra por las keywords configuradas."""
        try:
            response = self.http.get(API_URL, headers={"Accept": "application/json"})
            response.encoding = "utf-8"
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ScraperError(str(exc)) from exc

        offers: list[JobOffer] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            title = item.get("position") or item.get("title") or ""
            tags = " ".join(item.get("tags") or [])
            haystack = f"{title} {tags} {item.get('description', '')[:400]}"
            if keywords and not matches_any_keyword(haystack, keywords):
                continue
            salary_min = item.get("salary_min") or 0
            salary_max = item.get("salary_max") or 0
            salary = (
                f"USD {salary_min:,} - {salary_max:,} / año"
                if salary_min and salary_max
                else ""
            )
            offers.append(
                self.make_offer(
                    title=title,
                    company=item.get("company", ""),
                    location=item.get("location") or "Remoto (Worldwide)",
                    salary=salary,
                    url=item.get("url") or item.get("apply_url", ""),
                    description=item.get("description", ""),
                    is_remote=True,
                    posted_at=self.parse_datetime(item.get("date") or item.get("epoch")),
                )
            )
            if len(offers) >= self.max_offers:
                break
        return offers
