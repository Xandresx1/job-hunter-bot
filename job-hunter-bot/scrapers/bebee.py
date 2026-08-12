"""beBee Perú - listado con schema.org/ItemList + JobPosting en cada detalle."""
from __future__ import annotations

import re
from typing import Any

from core.models import JobOffer, normalize_text
from scrapers.base import BaseScraper, ScraperError


class BebeeScraper(BaseScraper):
    """Fuente NIVEL B: `bebee.com/pe/jobs?q=` devuelve ItemList con las URLs."""

    name = "bebee"
    label = "beBee Perú"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Obtiene las URLs del ItemList y lee el JSON-LD de los primeros detalles."""
        base_url = str(self.option("base_url", "https://bebee.com")).rstrip("/")
        country_path = str(self.option("country_path", "/pe"))
        max_details = int(self.option("max_details", 8))
        offers: list[JobOffer] = []
        errors: list[str] = []

        for keyword in keywords:
            list_url = f"{base_url}{country_path}/jobs"
            try:
                soup = self.http.get_soup(list_url, params={"q": keyword})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue

            urls: list[str] = []
            for element in self.http.find_item_list(soup):
                url = element.get("url") or (element.get("item") or {}).get("url")
                if url:
                    urls.append(str(url))
            if not urls:
                for anchor in soup.select(f'a[href*="{country_path}/jobs/"]'):
                    href = anchor.get("href", "")
                    if href and "/jobs/role/" not in href:
                        urls.append(href if href.startswith("http") else base_url + href)

            needle = normalize_text(keyword).split()[0] if keyword else ""
            # Prioriza URLs cuyo slug ya coincide con la keyword
            urls = sorted(
                dict.fromkeys(urls),
                key=lambda u: 0 if needle and needle in normalize_text(u) else 1,
            )
            for url in urls[:max_details]:
                offer = self._detail(url)
                if offer:
                    offers.append(offer)
            if len(offers) >= self.max_offers:
                break

        if not offers:
            raise ScraperError("; ".join(errors[:2]) or "sin ofertas en el ItemList")
        return offers

    def _detail(self, url: str) -> JobOffer | None:
        """Lee el JobPosting JSON-LD de una oferta."""
        try:
            soup = self.http.get_soup(url)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Detalle beBee falló %s: %s", url, exc)
            return None
        block = self.http.find_job_posting(soup)
        if block:
            offer = self.offer_from_json_ld(block, url)
            if offer:
                offer.source = self.name
                return offer
        heading = soup.select_one("h1")
        if not heading:
            return None
        slug = url.rstrip("/").split("/")[-1]
        return self.make_offer(
            title=heading.get_text(strip=True) or re.sub(r"-", " ", slug).title(),
            company="",
            location="Perú",
            url=url,
            description=soup.get_text(" ", strip=True)[:1500],
        )
