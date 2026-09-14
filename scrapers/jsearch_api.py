"""JSearch API (RapidAPI) - agrega Google for Jobs (LinkedIn, Indeed, Glassdoor...)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError, SkipSource

# El proveedor retiró /search (responde 404 "Endpoint '/search' does not exist").
# El endpoint vigente es /search-v2 y devuelve data = {"jobs": [...], "cursor": "..."}.
API_URL = "https://jsearch.p.rapidapi.com/search-v2"
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
            "x-rapidapi-key": self.env("RAPIDAPI_KEY"),
            "x-rapidapi-host": API_HOST,
            "Accept": "application/json",
        }
        country = str(self.option("country", "pe")).lower()
        language = str(self.option("language", "es")).lower()
        suffix = str(self.option("queries_suffix", "") or "").strip()
        num_pages = int(self.option("num_pages", 1))
        timeout = int(self.option("timeout_seconds", 60))

        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in search_keywords[:max_keywords]:
            params = {
                "query": f"{keyword} {suffix}".strip(),
                "page": "1",
                "num_pages": str(num_pages),
                "date_posted": self.option("date_posted", "week"),
                "country": country,
                "language": language,
            }
            try:
                response = self.http.get(API_URL, params=params, headers=headers, timeout=timeout)
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue

            if str(data.get("status", "OK")).upper() != "OK":
                errors.append(f"{keyword}: {(data.get('error') or {}).get('message', 'status ERROR')}")
                continue

            payload = data.get("data")
            # search-v2 -> {"jobs": [...]} ; compatibilidad con el formato antiguo (lista)
            items = (payload.get("jobs") or []) if isinstance(payload, dict) else (payload or [])

            for item in items[: self.max_offers]:
                location = ", ".join(
                    str(part)
                    for part in (item.get("job_city"), item.get("job_state"), item.get("job_country"))
                    if part
                )
                if not location:
                    # job_location viene como "Lima  •  a través de Bumeran": nos quedamos con la ciudad
                    location = str(item.get("job_location") or "").split("•")[0].strip()
                remote = bool(item.get("job_is_remote"))
                salary_min = item.get("job_min_salary")
                salary_max = item.get("job_max_salary")
                currency = item.get("job_salary_currency") or ""
                salary = (
                    f"{currency} {int(salary_min):,} - {int(salary_max):,}".strip()
                    if salary_min and salary_max
                    else str(item.get("job_salary_string") or "")
                )
                offers.append(
                    self.make_offer(
                        title=item.get("job_title", ""),
                        company=item.get("employer_name", ""),
                        location=("Remoto " if remote else "") + location,
                        salary=salary,
                        url=item.get("job_apply_link") or item.get("job_google_link") or "",
                        description=item.get("job_description", ""),
                        is_remote=remote,
                        country=item.get("job_country", ""),
                        posted_at=self.parse_datetime(
                            item.get("job_posted_at_datetime_utc")
                            or item.get("job_posted_at_timestamp")
                            or item.get("job_posted_at")
                        ),
                        raw={"publisher": item.get("job_publisher") or ""},
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
