"""JSearch API (RapidAPI) - agrega Google for Jobs (LinkedIn, Indeed, Glassdoor...)."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://jsearch.p.rapidapi.com/search"
API_HOST = "jsearch.p.rapidapi.com"


class JSearchScraper(BaseScraper):
    """Fuente NIVEL A (opcional): la forma más confiable de cubrir LinkedIn/Indeed."""

    name = "jsearch"
    label = "JSearch (Google for Jobs)"
    tier = "A"
    requires_env = ("RAPIDAPI_KEY",)

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword y devuelve las ofertas agregadas por Google for Jobs."""
        self.ensure_credentials()
        headers = {
            "X-RapidAPI-Key": self.env("RAPIDAPI_KEY"),
            "X-RapidAPI-Host": API_HOST,
            "Accept": "application/json",
        }
        suffix = self.option("queries_suffix", f"in {locations.get('country', 'Peru')} OR remote")
        num_pages = int(self.option("num_pages", 1))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            params = {
                "query": f"{keyword} {suffix}".strip(),
                "page": "1",
                "num_pages": str(num_pages),
                "date_posted": self.option("date_posted", "week"),
            }
            try:
                response = self.http.get(API_URL, params=params, headers=headers)
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for item in (data.get("data") or [])[: self.max_offers]:
                location = ", ".join(
                    str(part)
                    for part in (
                        item.get("job_city"),
                        item.get("job_state"),
                        item.get("job_country"),
                    )
                    if part
                )
                remote = bool(item.get("job_is_remote"))
                salary_min = item.get("job_min_salary")
                salary_max = item.get("job_max_salary")
                currency = item.get("job_salary_currency") or ""
                salary = (
                    f"{currency} {int(salary_min):,} - {int(salary_max):,}"
                    if salary_min and salary_max
                    else ""
                )
                publisher = item.get("job_publisher") or ""
                offers.append(
                    self.make_offer(
                        title=item.get("job_title", ""),
                        company=item.get("employer_name", ""),
                        location=("Remoto " if remote else "") + location,
                        salary=salary,
                        url=item.get("job_apply_link") or item.get("job_google_link", ""),
                        description=item.get("job_description", ""),
                        is_remote=remote,
                        country=item.get("job_country", ""),
                        posted_at=self.parse_datetime(
                            item.get("job_posted_at_datetime_utc")
                            or item.get("job_posted_at_timestamp")
                        ),
                        raw={"publisher": publisher},
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
