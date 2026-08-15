"""ConvocatoriasDeTrabajo.com - convocatorias del sector público peruano.

Buscador público: GET /buscar-empleo.php?q={keyword}&dep={departamento}
Departamentos: 0 = todo el país, 4 = Arequipa (mismo orden alfabético del sitio).
Las tarjetas (article.puesto) incluyen el REQUISITO DE GRADO (ej. "TÍTULO DE
LICENCIADO(A) EN NUTRICIÓN"), sueldo y fecha límite: se agregan a la descripción
para que el matcher pueda evaluar egresado/bachiller/titulado.
"""
from __future__ import annotations

from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class ConvocatoriasScraper(BaseScraper):
    """Fuente NIVEL B: HTML estático simple, sin Cloudflare (verificado 2026)."""

    name = "convocatorias"
    label = "ConvocatoriasDeTrabajo"
    tier = "B"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Busca cada keyword en el departamento configurado y parsea las tarjetas."""
        base_url = str(
            self.option("base_url", "https://www.convocatoriasdetrabajo.com")
        ).rstrip("/")
        # 4 = Arequipa. Usa 0 para todo el país.
        department_id = str(self.option("department_id", "4"))
        # Este portal es de sector público peruano: por defecto solo tiene sentido
        # buscar keywords en español definidas en source_options.
        search_keywords = self.option("keywords_override") or keywords
        default_location = str(self.option("default_location", "Arequipa, Perú"))

        offers: list[JobOffer] = []
        errors: list[str] = []
        seen_urls: set[str] = set()
        for keyword in search_keywords:
            try:
                soup = self.http.get_soup(
                    f"{base_url}/buscar-empleo.php",
                    params={"q": keyword, "dep": department_id},
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for card in soup.select("article.puesto"):
                anchor = card.select_one("h3 a[href]")
                if not anchor:
                    continue
                href = str(anchor.get("href", ""))
                url = href if href.startswith("http") else f"{base_url}/{href.lstrip('/')}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = anchor.get_text(" ", strip=True)
                detail = card.select_one("div.puesto-det")
                company = ""
                requirement = ""
                location_line = ""
                deadline = ""
                salary = ""
                if detail:
                    paragraphs = detail.find_all("p")
                    if paragraphs:
                        company = paragraphs[0].get_text(" ", strip=True)
                    for paragraph in paragraphs[1:]:
                        text = paragraph.get_text(" ", strip=True)
                        if paragraph.select_one("span.icon-grado"):
                            requirement = text
                        elif paragraph.select_one("span.icon-mapa1"):
                            location_line = text
                            if "soles" in text.lower():
                                for chunk in text.split("|"):
                                    if "soles" in chunk.lower():
                                        salary = chunk.strip()
                        elif paragraph.select_one("span.icon-calendario"):
                            deadline = text
                location = (
                    location_line.split("|")[0].strip() if location_line else default_location
                )
                description_parts = [
                    part
                    for part in (company, f"Requisito: {requirement}" if requirement else "", deadline)
                    if part
                ]
                offers.append(
                    self.make_offer(
                        title=title,
                        company=company,
                        location=location or default_location,
                        salary=salary,
                        url=url,
                        description=". ".join(description_parts) or title,
                    )
                )
                if len(offers) >= self.max_offers:
                    break
            if len(offers) >= self.max_offers:
                break
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
