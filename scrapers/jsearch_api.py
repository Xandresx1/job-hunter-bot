"""JSearch API (RapidAPI) - agrega Google for Jobs (LinkedIn, Indeed, Glassdoor...)."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError, SkipSource

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
        # Control de cuota (plan gratuito RapidAPI = 200 requests/mes):
        # solo corre en las horas UTC configuradas.
        run_hours = self.option("run_hours_utc") or []
        if run_hours:
            current_hour = datetime.now(timezone.utc).hour
            if current_hour not in [int(h) for h in run_hours]:
                raise SkipSource(
                    f"fuera de las horas configuradas (hora UTC actual: {current_hour}, "
                    f"corre en: {run_hours})"
                )
        search_keywords = self.option("keywords_override") or keywords
        max_keywords = int(self.option("max_keywords_per_cycle", len(search_keywords)))
        headers = {
            "X-RapidAPI-Key": self.env("RAPIDAPI_KEY"),
            "X-RapidAPI-Host": API_HOST,
            "Accept": "application/json",
        }
        suffix = self.option("queries_suffix", f"in {locations.get('country', 'Peru')} OR remote")
        num_pages = int(self.option("num_pages", 1))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in search_keywords[:max_keywords]:
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
