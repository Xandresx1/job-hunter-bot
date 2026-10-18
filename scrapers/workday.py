"""Workday Careers - API interna JSON de los portales *.myworkdayjobs.com (verificado oct 2026).

Sirve para la página de empleos de Workday (https://workday.wd5.myworkdayjobs.com/Workday)
y, en general, para CUALQUIER empresa que use Workday como portal de empleo: solo hay
que agregar su `host`, `tenant` y `site` en `source_options.workday.sites`.

Endpoints reales (los usa la propia web, no requieren login ni API key):
  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
       body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
       -> {"total": N, "jobPostings": [{title, externalPath, locationsText, postedOn, remoteType}], "facets": [...]}
  GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
       -> {"jobPostingInfo": {title, jobDescription(HTML), location, startDate, remoteType, externalUrl, ...}}

NOTA: el buscador de Workday es "difuso" (buscar 'junior' devuelve también 'Sr'), por eso
por defecto se descargan TODAS las ofertas (`search_text: ""`, ~20 páginas) y se
pre-filtran aquí por título; el matcher del bot hace el resto.
"""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, clean_html, normalize_text
from scrapers.base import BaseScraper, ScraperError

DEFAULT_SITES: list[dict[str, str]] = [
    {"host": "workday.wd5.myworkdayjobs.com", "tenant": "workday", "site": "Workday", "company": "Workday, Inc."},
]

# Términos (normalizados) que deben aparecer en el TÍTULO para considerar la oferta.
DEFAULT_TITLE_TERMS: list[str] = [
    "junior", "jr", "intern", "internship", "graduate", "early career", "entry level",
    "associate", "trainee", "apprentice", "new grad", "university", "student",
    "practicante", "becario", "generation workday",
]


class WorkdayScraper(BaseScraper):
    """Fuente NIVEL A: API JSON pública de portales Workday."""

    name = "workday"
    label = "Workday Careers"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Recorre cada sitio Workday configurado y devuelve las ofertas pre-filtradas."""
        sites = self.option("sites") or DEFAULT_SITES
        page_size = min(20, max(1, int(self.option("page_size", 20))))  # Workday acepta máx. 20
        max_pages = max(1, int(self.option("max_pages", 25)))
        search_text = str(self.option("search_text", "") or "")
        title_terms = [normalize_text(t) for t in (self.option("title_terms") or DEFAULT_TITLE_TERMS) if t]
        exclude_title = [normalize_text(t) for t in (self.option("exclude_title_terms") or ["senior", "sr ", "sr.", "principal", "lead", "manager", "director", "staff"]) if t]
        wanted_countries = {normalize_text(c) for c in (self.option("countries") or []) if c}
        flex_is_remote = bool(self.option("treat_flex_as_remote", False))
        max_details = int(self.option("max_details", 15))

        offers: list[JobOffer] = []
        errors: list[str] = []
        seen_ids: set[str] = set()
        details_left = max_details

        for site in sites:
            host = str(site.get("host", "")).strip().rstrip("/")
            tenant = str(site.get("tenant", "")).strip()
            site_name = str(site.get("site", "")).strip()
            if not (host and tenant and site_name):
                errors.append(f"sitio inválido en config: {site}")
                continue
            api_base = f"https://{host}/wday/cxs/{tenant}/{site_name}"
            public_base = f"https://{host}/{site_name}"
            company_default = str(site.get("company") or tenant.title())
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": f"https://{host}",
                "Referer": f"{public_base}/",
            }

            applied_facets: dict[str, Any] = {}
            offset = 0
            total = None
            for _page in range(max_pages):
                payload = {
                    "appliedFacets": applied_facets,
                    "limit": page_size,
                    "offset": offset,
                    "searchText": search_text,
                }
                try:
                    data = self.http.post(f"{api_base}/jobs", json=payload, headers=headers).json()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{host} offset {offset}: {exc}")
                    break

                # En la primera página resolvemos los IDs de país (facet Location_Country)
                if offset == 0 and wanted_countries:
                    ids = self._country_facet_ids(data.get("facets") or [], wanted_countries)
                    if ids:
                        applied_facets = {"Location_Country": ids}
                        self.log.info("[%s] filtro de país aplicado: %s", host, sorted(wanted_countries))
                        continue  # repetimos la página 0 ya filtrada

                # Workday solo informa `total` en la primera página (luego devuelve 0)
                if total is None:
                    total = int(data.get("total") or 0)
                postings = data.get("jobPostings") or []
                if not postings:
                    break

                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("externalPath") or "")
                    title = str(item.get("title") or "").strip()
                    if not path or not title:
                        continue
                    job_id = (item.get("bulletFields") or [path])[0]
                    key = f"{host}:{job_id}"
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)

                    norm_title = normalize_text(title)
                    if exclude_title and any(t in f" {norm_title} " for t in exclude_title):
                        continue
                    if title_terms and not any(t in norm_title for t in title_terms):
                        continue

                    remote_type = str(item.get("remoteType") or "")
                    is_remote = remote_type.lower() == "remote" or (flex_is_remote and remote_type.lower() == "flex")
                    location = str(item.get("locationsText") or "").replace(".", ", ")
                    description = ""
                    company = company_default

                    # Detalle (descripción completa) solo para las que pasaron el pre-filtro
                    if details_left > 0:
                        detail = self._fetch_detail(api_base, path, headers)
                        details_left -= 1
                        if detail:
                            info = detail.get("jobPostingInfo") or {}
                            description = clean_html(info.get("jobDescription") or "")
                            location = str(info.get("location") or location)
                            org = detail.get("hiringOrganization") or {}
                            company = str(org.get("name") or company)
                            if info.get("remoteType"):
                                rt = str(info["remoteType"]).lower()
                                is_remote = rt == "remote" or (flex_is_remote and rt == "flex")

                    offers.append(
                        self.make_offer(
                            title=title,
                            company=company,
                            location=location or "Remote",
                            url=f"{public_base}{path}",
                            description=description or f"{title} - {location} - {item.get('postedOn', '')}",
                            is_remote=is_remote,
                            posted_at=self.parse_datetime(str(item.get("postedOn") or "").replace("Posted ", "")),
                            raw={"remoteType": remote_type, "jobReqId": str(job_id), "site": host},
                        )
                    )
                    if len(offers) >= self.max_offers:
                        return offers

                offset += page_size
                if total is not None and offset >= total:
                    break

        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        if errors:
            self.log.warning("fallos parciales: %s", "; ".join(errors[:3]))
        return offers

    # --------------------------------------------------------------- helpers
    def _fetch_detail(self, api_base: str, path: str, headers: dict[str, str]) -> dict[str, Any] | None:
        """GET del detalle de una oferta (JSON). Best-effort: nunca lanza excepción."""
        try:
            return self.http.get(f"{api_base}{path}", headers={"Accept": "application/json", "Referer": headers["Referer"]}).json()
        except Exception as exc:  # noqa: BLE001
            self.log.debug("sin detalle para %s: %s", path, exc)
            return None

    @staticmethod
    def _country_facet_ids(facets: list[dict[str, Any]], wanted: set[str]) -> list[str]:
        """Traduce nombres de país ('Spain', 'Mexico') a los IDs del facet Location_Country."""
        for facet in facets:
            if facet.get("facetParameter") != "Location_Country":
                continue
            ids: list[str] = []
            for value in facet.get("values") or []:
                descriptor = normalize_text(value.get("descriptor", ""))
                if any(w in descriptor or descriptor in w for w in wanted):
                    ids.append(str(value.get("id")))
            return ids
        return []
