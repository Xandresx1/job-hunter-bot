"""Google Careers - página pública renderizada en servidor (sin API oficial)."""
from __future__ import annotations

import re
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError

SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results"
BASE = "https://www.google.com/about/careers/applications/"


class GoogleCareersScraper(BaseScraper):
    """Fuente NIVEL A: Google no publica API JSON, pero el HTML es parseable.

    Verificado en POC: la página de resultados incluye los enlaces
    `jobs/results/<id>-<slug>` con la empresa y la ubicación en el mismo card.
    """

    name = "google_careers"
    label = "Google Careers"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca las primeras keywords y parsea los cards de resultados."""
        max_keywords = int(self.option("max_keywords_per_cycle", 3))
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords[:max_keywords]:
            try:
                soup = self.http.get_soup(
                    SEARCH_URL, params={"q": keyword, "sort_by": "date"}
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for anchor in soup.select('a[href*="jobs/results/"]'):
                href = anchor.get("href", "").split("?")[0]
                if not href or href.endswith("jobs/results/"):
                    continue
                url = href if href.startswith("http") else BASE + href.lstrip("/")
                card = anchor
                for _ in range(6):
                    card = card.parent or card
                    if card.name in ("li", "div") and len(card.get_text(strip=True)) > 80:
                        break
                chunks = [c.strip() for c in card.get_text("|", strip=True).split("|") if c.strip()]
                company = self._after(chunks, "corporate_fare")
                location = self._after(chunks, "place")
                slug = href.rstrip("/").split("/")[-1]
                title = re.sub(r"^\d+-", "", slug).replace("-", " ").strip().title()
                description = " ".join(chunks[:12])
                offers.append(
                    self.make_offer(
                        title=title,
                        company=company or "Google",
                        location=location or "",
                        url=url,
                        description=description,
                    )
                )
                if len(offers) >= self.max_offers:
                    break
        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers

    @staticmethod
    def _after(chunks: list[str], marker: str) -> str:
        """Devuelve el texto que sigue a un icono/marcador dentro del card."""
        if marker in chunks:
            index = chunks.index(marker)
            if index + 1 < len(chunks):
                return chunks[index + 1]
        return ""
