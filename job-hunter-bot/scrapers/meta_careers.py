"""Meta Careers - endpoint GraphQL público de búsqueda de vacantes."""
from __future__ import annotations

import json
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

GRAPHQL_URL = "https://www.metacareers.com/api/graphql/"
JOB_URL = "https://www.metacareers.com/jobs/{job_id}/"


class MetaCareersScraper(BaseScraper):
    """Fuente NIVEL A: GraphQL público de metacareers.com.

    Nota operativa: Meta bloquea con HTTP 400 muchas IPs de datacenter. Si eso
    pasa, la fuente se desactiva sola (circuit breaker) y sus vacantes se
    cubren vía JSearch/Google for Jobs.
    """

    name = "meta_careers"
    label = "Meta Careers"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta el GraphQL de búsqueda con el doc_id configurado."""
        doc_id = str(self.option("doc_id", "9114524511922157"))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords[: int(self.option("max_keywords_per_cycle", 2))]:
            variables = {
                "search_input": {
                    "q": keyword,
                    "divisions": [],
                    "offices": [],
                    "roles": [],
                    "leadership_levels": [],
                    "saved_jobs": [],
                    "saved_searches": [],
                    "sub_teams": [],
                    "teams": [],
                    "is_leadership": False,
                    "is_remote_only": False,
                    "sort_by_new": True,
                    "results_per_page": 50,
                }
            }
            payload = {
                "fb_api_req_friendly_name": "CareersJobSearchResultsDataQuery",
                "variables": json.dumps(variables),
                "doc_id": doc_id,
            }
            try:
                response = self.http.post(
                    GRAPHQL_URL,
                    data=payload,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "*/*",
                        "X-FB-Friendly-Name": "CareersJobSearchResultsDataQuery",
                        "Origin": "https://www.metacareers.com",
                        "Referer": "https://www.metacareers.com/jobs",
                    },
                )
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            jobs = self._extract_jobs(data)
            for item in jobs[: self.max_offers]:
                job_id = item.get("id") or item.get("job_id") or ""
                offices = item.get("locations") or item.get("offices") or []
                location = ", ".join(str(o) for o in offices if o)
                offers.append(
                    self.make_offer(
                        title=item.get("title", ""),
                        company="Meta",
                        location=location,
                        url=item.get("url") or JOB_URL.format(job_id=job_id),
                        description=item.get("description", "") or "",
                    )
                )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers

    @staticmethod
    def _extract_jobs(data: Any) -> list[dict[str, Any]]:
        """Localiza la lista de vacantes dentro de la respuesta GraphQL."""
        payload = (data or {}).get("data") or {}
        for key in ("job_search_with_featured_jobs", "job_search"):
            node = payload.get(key)
            if isinstance(node, dict):
                jobs = node.get("all_jobs") or node.get("jobs") or []
                if isinstance(jobs, list):
                    return [j for j in jobs if isinstance(j, dict)]
            if isinstance(node, list):
                return [j for j in node if isinstance(j, dict)]
        return []
