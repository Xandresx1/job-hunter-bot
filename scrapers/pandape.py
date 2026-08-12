"""Pandapé (portal de empleo de NTT Data Perú) - listado HTML + JSON-LD."""
from __future__ import annotations

from typing import Any

from core.models import JobOffer, matches_any_keyword
from scrapers.base import BaseScraper, ScraperError


class PandapeScraper(BaseScraper):
    """Fuente NIVEL B: cards `a[href^=/Detail/]`; el detalle trae JobPosting JSON-LD."""

    name = "pandape_nttdata"
    label = "NTT Data (Pandapé)"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Lista las vacantes publicadas y filtra por keywords antes del detalle."""
        base_url = str(
            self.option("base_url", "https://ntt-data.pandape.computrabajo.com")
        ).rstrip("/")
        list_url = f"{base_url}/Vacancies"
        try:
            soup = self.http.get_soup(list_url)
        except Exception as exc:  # noqa: BLE001
            raise ScraperError(str(exc)) from exc

        candidates: list[tuple[str, str, str]] = []
        for anchor in soup.select('a[href^="/Detail/"]'):
            heading = anchor.select_one("h3")
            title = heading.get_text(strip=True) if heading else anchor.get_text(" ", strip=True)
            url = base_url + anchor.get("href", "")
            card_text = anchor.get_text(" ", strip=True)
            candidates.append((title, url, card_text))

        if not candidates:
            raise ScraperError("listado vacío o HTML cambiado (sin enlaces /Detail/)")

        matched = [
            c for c in candidates if not keywords or matches_any_keyword(c[2], keywords)
        ]
        max_details = int(self.option("max_details", 8))
        offers: list[JobOffer] = []
        for title, url, card_text in matched[:max_details]:
            offer = self._detail(url) or self.make_offer(
                title=title, company="NTT Data Perú", location="Perú", url=url,
                description=card_text,
            )
            offer.source = self.name
            if not offer.company:
                offer.company = "NTT Data Perú"
            offers.append(offer)

        # Si nada coincidió con las keywords devolvemos los títulos crudos:
        # el matcher aplicará el scoring y los descartará si no aplican.
        if not offers:
            for title, url, card_text in candidates[: self.max_offers]:
                offers.append(
                    self.make_offer(
                        title=title,
                        company="NTT Data Perú",
                        location="Perú",
                        url=url,
                        description=card_text,
                    )
                )
        return offers

    def _detail(self, url: str) -> JobOffer | None:
        """Obtiene la vacante desde el JSON-LD del detalle."""
        try:
            soup = self.http.get_soup(url)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Detalle Pandapé falló %s: %s", url, exc)
            return None
        block = self.http.find_job_posting(soup)
        if block:
            return self.offer_from_json_ld(block, url)
        heading = soup.select_one("h1")
        if not heading:
            return None
        return self.make_offer(
            title=heading.get_text(strip=True),
            company="NTT Data Perú",
            location="Perú",
            url=url,
            description=soup.get_text(" ", strip=True)[:2000],
        )
