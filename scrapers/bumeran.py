"""Bumeran Perú - endpoint interno JSON `searchNormalizado` (verificado 2026)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


def _slugify(text: str) -> str:
    """Convierte un título en el slug que Bumeran usa en las URLs de detalle."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


class BumeranScraper(BaseScraper):
    """Fuente NIVEL A: API interna JSON de Bumeran.

    Endpoint real (extraído del bundle JS del sitio):
      POST /api/avisos/searchNormalizado?pageSize=N&page=N
      body: {"filtros": [], "busquedaExtendida": false, "query": kw, "tipoDetalle": "full"}
      header obligatorio: x-site-id: BMPE
    """

    name = "bumeran"
    label = "Bumeran Perú"
    tier = "A"

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta la API interna por cada keyword y deduplica por id de aviso."""
        base_url = str(self.option("base_url", "https://www.bumeran.com.pe")).rstrip("/")
        page_size = int(self.option("page_size", 20))
        url = f"{base_url}/api/avisos/searchNormalizado?pageSize={page_size}&page=0"
        offers: list[JobOffer] = []
        errors: list[str] = []
        seen_ids: set[str] = set()
        for keyword in keywords:
            payload = {
                "filtros": [],
                "busquedaExtendida": False,
                "query": keyword,
                "tipoDetalle": "full",
            }
            try:
                response = self.http.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-site-id": str(self.option("site_id", "BMPE")),
                        "Origin": base_url,
                        "Referer": f"{base_url}/empleos.html",
                    },
                )
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{keyword}: {exc}")
                continue
            for item in (data.get("content") or [])[: self.max_offers]:
                if not isinstance(item, dict):
                    continue
                aviso_id = str(item.get("id") or "")
                if not aviso_id or aviso_id in seen_ids:
                    continue
                seen_ids.add(aviso_id)
                title = str(item.get("titulo") or "")
                empresa = item.get("empresa")
                company = (
                    empresa.get("denominacion", "")
                    if isinstance(empresa, dict)
                    else str(empresa or "")
                )
                offers.append(
                    self.make_offer(
                        title=title,
                        company=company,
                        location=str(item.get("localizacion") or "") or "Perú",
                        salary=str(item.get("salario") or ""),
                        url=f"{base_url}/empleos/{_slugify(title)}-{aviso_id}.html",
                        description=str(item.get("detalle") or ""),
                        posted_at=self.parse_datetime(
                            item.get("fechaHoraPublicacion") or item.get("fechaPublicacion")
                        ),
                    )
                )
            if len(offers) >= self.max_offers:
                break
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        if errors:
            # Fallos parciales: hubo ofertas, pero alguna keyword falló.
            # Se loguea para no perder visibilidad (no activa el circuit breaker).
            self.log.warning(
                "fallos parciales en %s keyword(s): %s",
                len(errors),
                "; ".join(errors[:3]),
            )
        return offers
