"""LinkedIn (endpoint público guest) - máximo 1 request por minuto y backoff."""
from __future__ import annotations

import time
from typing import Any

from core.http_client import HttpError
from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

GUEST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)


class LinkedInBlocked(ScraperError):
    """LinkedIn respondió 429/999: hay que desactivar la fuente 6 horas."""


class LinkedInGuestScraper(BaseScraper):
    """Fuente NIVEL C: usa el endpoint público de búsqueda para invitados.

    Reglas anti-bloqueo aplicadas:
      * máximo 1 request por minuto (configurable);
      * pocas keywords por ciclo;
      * si devuelve 429 o 999 se lanza LinkedInBlocked y el runner desactiva
        la fuente durante 6 horas.
    """

    name = "linkedin_guest"
    label = "LinkedIn (guest)"
    tier = "C"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta pocas combinaciones keyword/ubicación con 1 request por minuto."""
        max_keywords = int(self.option("max_keywords_per_cycle", 2))
        search_locations = self.option(
            "locations",
            [
                locations.get("country", "Peru"),
                f"{locations.get('local_city', '')}, {locations.get('country', '')}".strip(", "),
            ],
        )
        seconds_between = int(self.option("seconds_between_requests", 60))
        offers: list[JobOffer] = []
        errors: list[str] = []
        requests_done = 0

        for keyword in keywords[:max_keywords]:
            for location in [loc for loc in search_locations if loc]:
                if requests_done:
                    time.sleep(seconds_between)
                requests_done += 1
                params = {
                    "keywords": keyword,
                    "location": location,
                    "start": 0,
                    "f_TPR": self.option("time_filter", "r604800"),  # última semana
                }
                try:
                    soup = self.http.get_soup(
                        GUEST_URL,
                        params=params,
                        headers={
                            "Accept": "text/html,application/xhtml+xml",
                            "Referer": "https://www.linkedin.com/jobs",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    )
                except HttpError as exc:
                    message = str(exc)
                    if "429" in message or "999" in message:
                        raise LinkedInBlocked(message) from exc
                    errors.append(f"{keyword}/{location}: {message}")
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{keyword}/{location}: {exc}")
                    continue

                for item in soup.select("li"):
                    title_tag = item.select_one("h3")
                    company_tag = item.select_one("h4")
                    location_tag = item.select_one("span.job-search-card__location")
                    time_tag = item.select_one("time")
                    link = item.select_one("a.base-card__full-link") or item.select_one("a[href]")
                    if not title_tag or not link:
                        continue
                    url = link.get("href", "").split("?")[0]
                    offers.append(
                        self.make_offer(
                            title=title_tag.get_text(strip=True),
                            company=company_tag.get_text(strip=True) if company_tag else "",
                            location=location_tag.get_text(strip=True)
                            if location_tag
                            else location,
                            url=url,
                            description=item.get_text(" ", strip=True),
                            posted_at=self.parse_datetime(
                                (time_tag.get("datetime") if time_tag else "")
                                or (time_tag.get_text(strip=True) if time_tag else "")
                            ),
                        )
                    )
                    if len(offers) >= self.max_offers:
                        break
        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers
