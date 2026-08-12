"""Microsoft Careers - endpoint JSON público de búsqueda (gcsservices)."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

API_URL = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
JOB_URL = "https://jobs.careers.microsoft.com/global/en/job/{job_id}"
FALLBACK_SEARCH = "https://jobs.careers.microsoft.com/global/en/search"


class MicrosoftCareersScraper(BaseScraper):
    """Fuente NIVEL A: JSON no documentado.

    Nota operativa: desde algunas IPs de datacenter Microsoft corta la conexión
    TLS o responde 404. Si ocurre, la fuente degrada elegantemente (circuit
    breaker) y sus vacantes se cubren vía JSearch.
    """

    name = "microsoft_careers"
    label = "Microsoft Careers"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta el endpoint de búsqueda; si falla intenta el JSON-LD del HTML."""
        max_keywords = int(self.option("max_keywords_per_cycle", 2))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords[:max_keywords]:
            params = {
                "q": keyword,
                "l": self.option("language", "en_us"),
                "pg": 1,
                "pgSz": int(self.option("page_size", 20)),
                "o": "Recent",
                "flt": "true",
            }
            try:
                data = self.http.get_json(API_URL, params=params)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                offers.extend(self._fallback_html(keyword))
                continue
            result = ((data or {}).get("operationResult") or {}).get("result") or {}
            for item in (result.get("jobs") or [])[: self.max_offers]:
                properties = item.get("properties") or {}
                locations_text = ", ".join(
                    str(loc) for loc in (properties.get("locations") or []) if loc
                ) or properties.get("primaryLocation", "")
                job_id = item.get("jobId") or item.get("id") or ""
                offers.append(
                    self.make_offer(
                        title=item.get("title", ""),
                        company="Microsoft",
                        location=locations_text,
                        url=JOB_URL.format(job_id=job_id),
                        description=properties.get("description", "")
                        or item.get("description", ""),
                        is_remote=str(properties.get("workSiteFlexibility", "")).lower().find(
                            "remote"
                        )
                        >= 0,
                        posted_at=self.parse_datetime(item.get("postingDate")),
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers

    def _fallback_html(self, keyword: str) -> list[JobOffer]:
        """Intenta extraer JSON-LD JobPosting desde la página pública de búsqueda."""
        try:
            soup = self.soup(FALLBACK_SEARCH, params={"q": keyword}, wait_selector="h2")
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Fallback HTML de Microsoft falló: %s", exc)
            return []
        offers: list[JobOffer] = []
        for block in self.http.json_ld_blocks(soup):
            if str(block.get("@type", "")).lower() != "jobposting":
                continue
            offer = self.offer_from_json_ld(block, FALLBACK_SEARCH)
            if offer:
                offers.append(offer)
        return offers
