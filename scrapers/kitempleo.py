"""Kitempleo Perú - buscador HTML (verificado octubre 2026).

IMPORTANTE: el buscador de Kitempleo es un formulario POST que redirige a una URL
SEO con GET:  https://www.kitempleo.pe/search/<palabras+con+mas>/<pagina>/
La versión anterior usaba `/search/?keywords=...` (GET con query string), que el
sitio IGNORA y devuelve el listado genérico "Empleos en Perú". Por eso casi nunca
llegaban ofertas útiles de esta fuente.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


def _slug(keyword: str) -> str:
    """'practicante desarrollo de software' -> 'practicante+desarrollo+de+software'."""
    words = [w for w in re.split(r"\s+", keyword.strip()) if w]
    return "+".join(quote(w, safe="") for w in words)


class KitempleoScraper(BaseScraper):
    """Fuente NIVEL B: cards con título, fecha, empresa, ubicación y resumen."""

    name = "kitempleo"
    label = "Kitempleo"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword (N páginas) usando la URL SEO del buscador."""
        base_url = str(self.option("base_url", "https://www.kitempleo.pe")).rstrip("/")
        max_pages = max(1, int(self.option("max_pages", 2)))
        # Permite fijar las búsquedas exactas desde config (ej. "nutricionista arequipa")
        # y/o añadir búsquedas extra a las keywords globales.
        search_keywords: list[str] = list(self.option("keywords_override") or keywords)
        for extra in self.option("extra_keywords") or []:
            if extra and extra not in search_keywords:
                search_keywords.append(extra)

        offers: list[JobOffer] = []
        errors: list[str] = []
        seen_urls: set[str] = set()

        for keyword in search_keywords:
            slug = _slug(keyword)
            if not slug:
                continue
            for page in range(1, max_pages + 1):
                url = f"{base_url}/search/{slug}/" + (f"{page}/" if page > 1 else "")
                try:
                    soup = self.http.get_soup(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{keyword} p{page}: {exc}")
                    break

                cards = soup.select('a[href*="/empleo/"]')
                new_in_page = 0
                for anchor in cards:
                    offer = self._parse_card(anchor, base_url)
                    if offer is None or offer.url in seen_urls:
                        continue
                    seen_urls.add(offer.url)
                    offers.append(offer)
                    new_in_page += 1
                    if len(offers) >= self.max_offers:
                        return offers
                # Página vacía o repetida -> no hay más resultados para esta keyword
                if new_in_page == 0:
                    break

        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        if errors:
            self.log.warning("fallos parciales en %s búsqueda(s): %s", len(errors), "; ".join(errors[:3]))
        return offers

    # ----------------------------------------------------------------- parse
    def _parse_card(self, anchor: Any, base_url: str) -> JobOffer | None:
        """Convierte una card `<a href="/empleo/ID/slug">` en JobOffer."""
        href = anchor.get("href", "")
        if not href or "/empleo/" not in href:
            return None
        url = href if href.startswith("http") else base_url + href

        heading = anchor.select_one("h3") or anchor.select_one("h4")
        title = heading.get_text(" ", strip=True) if heading else ""
        if not title:
            return None

        # Atributos: fecha (fa-calendar) | empresa (fa-pencil) | ubicación (fa-map-marker)
        date_text = self._attrib(anchor, "fa-calendar")
        company = self._attrib(anchor, "fa-pencil")
        location = self._attrib(anchor, "fa-map-marker")

        summary_tag = anchor.select_one("div.marked p") or anchor.select_one("div.marked")
        summary = summary_tag.get_text(" ", strip=True) if summary_tag else ""
        summary = summary.replace("[...]", "").strip()

        return self.make_offer(
            title=title,
            company=company,
            location=f"{location}, Perú" if location and "per" not in location.lower() else (location or "Perú"),
            url=url,
            description=summary or title,
            posted_at=self.parse_datetime(date_text),
        )

    @staticmethod
    def _attrib(anchor: Any, icon_class: str) -> str:
        """Texto del atributo cuyo icono tiene la clase dada (ej. 'fa-calendar')."""
        icon = anchor.select_one(f"i.{icon_class}")
        if icon is None or icon.parent is None:
            return ""
        return icon.parent.get_text(" ", strip=True)
