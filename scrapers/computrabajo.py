"""Computrabajo (Perú + otros países) - scraping HTML del listado y detalle."""
from __future__ import annotations

import re
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

from core.models import JobOffer, strip_accents
from scrapers.base import BaseScraper, ScraperError


def slugify(text: str) -> str:
    """Convierte 'Junior Developer' en 'junior-developer' para las URLs."""
    clean = strip_accents(text).lower()
    clean = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    return clean


class ComputrabajoScraper(BaseScraper):
    """Fuente NIVEL B: HTML estable, incluye salario, modalidad y antigüedad."""

    name = "computrabajo"
    label = "Computrabajo"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en el dominio principal y en los dominios extra."""
        base_url = str(self.option("base_url", "https://pe.computrabajo.com")).rstrip("/")
        domains = [base_url] + [
            str(d).rstrip("/") for d in (self.option("extra_domains", []) or [])
        ]
        city = str(locations.get("local_city", "") or "")
        offers: list[JobOffer] = []
        errors: list[str] = []

        for domain in domains:
            for keyword in keywords:
                slug = slugify(keyword)
                if not slug:
                    continue
                urls = [f"{domain}/trabajo-de-{slug}"]
                if domain == base_url and city:
                    urls.append(f"{domain}/trabajo-de-{slug}-en-{slugify(city)}")
                for url in urls:
                    try:
                        soup = self.http.get_soup(url)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{url}: {exc}")
                        continue
                    offers.extend(self._parse_list(soup, domain))
                    if len(offers) >= self.max_offers:
                        break

        # Enriquecer con la descripción del detalle en las primeras ofertas
        max_details = int(self.option("max_details_per_keyword", 5))
        for offer in offers[:max_details]:
            description = self._fetch_description(offer.url)
            if description:
                offer.description = description

        if not offers and errors:
            raise ScraperError("; ".join(errors[:2]))
        return offers

    # ------------------------------------------------------------------ parse
    def _parse_list(self, soup: BeautifulSoup, domain: str) -> list[JobOffer]:
        """Parsea los cards `article.box_offer` del listado."""
        results: list[JobOffer] = []
        for card in soup.select("article.box_offer"):
            link = card.select_one("h2 a")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "").split("#")[0]
            url = href if href.startswith("http") else domain + href
            company_tag = card.select_one('p a[offer-grid-article-company-url], p.dFlex a')
            company = company_tag.get_text(strip=True) if company_tag else ""
            location = self._card_location(card)
            salary, modality = self._card_meta(card)
            age_tag = card.select_one("p.fs13.fc_aux")
            posted_at = self.parse_datetime(age_tag.get_text(strip=True)) if age_tag else None
            summary = card.get_text(" ", strip=True)
            results.append(
                self.make_offer(
                    title=title,
                    company=company,
                    location=location,
                    salary=salary,
                    url=url,
                    description=summary,
                    is_remote=self.looks_remote(f"{modality} {location} {title}"),
                    posted_at=posted_at,
                )
            )
        return results

    @staticmethod
    def _card_location(card: Tag) -> str:
        """Ubicación del card, ignorando el bloque de valoración de la empresa."""
        for paragraph in card.select("p.fs16.fc_base.mt5"):
            span = paragraph.select_one("span.mr10") or paragraph.select_one("span")
            if not span:
                continue
            text = span.get_text(" ", strip=True)
            if not text:
                continue
            # Descarta valoraciones tipo "4,4" / "3.8" y textos numéricos
            if re.fullmatch(r"[\d.,]+", text):
                continue
            if paragraph.select_one("span.vm_fx, span.i_star, span.icon_start"):
                continue
            return text
        return ""

    @staticmethod
    def _card_meta(card: Tag) -> tuple[str, str]:
        """Extrae (salario, modalidad) del bloque de iconos del card."""
        salary = ""
        modality = ""
        for span in card.select("div.fs13 span.dIB"):
            text = span.get_text(" ", strip=True)
            icon = span.select_one("span.icon")
            classes = " ".join(icon.get("class", [])) if icon else ""
            if "i_salary" in classes:
                salary = text
            elif "home_office" in classes or "i_modality" in classes:
                modality = text
        return salary, modality

    def _fetch_description(self, url: str) -> Optional[str]:
        """Descarga el detalle y devuelve la descripción más larga (`p.mbB`)."""
        if not url:
            return None
        try:
            soup = self.http.get_soup(url)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("No se pudo abrir el detalle %s: %s", url, exc)
            return None
        block = self.http.find_job_posting(soup)
        if block and block.get("description"):
            return str(block["description"])
        candidates = [p.get_text(" ", strip=True) for p in soup.select("p.mbB")]
        candidates += [d.get_text(" ", strip=True) for d in soup.select("div.box_detail")]
        candidates = [c for c in candidates if len(c) > 120]
        return max(candidates, key=len) if candidates else None
