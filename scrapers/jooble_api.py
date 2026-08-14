"""Jooble API (https://{host}/api/{key}) - requiere JOOBLE_API_KEY.

IMPORTANTE: las API keys de Jooble estan atadas al pais donde te registras.
Una key creada en pe.jooble.org SOLO funciona contra pe.jooble.org (403 en el resto).
Configura el host en config.yaml -> source_options.jooble.host
"""
from __future__ import annotations

import json
from typing import Any

from core.models import JobOffer
from scrapers.base import BaseScraper, ScraperError


class JoobleScraper(BaseScraper):
    """Fuente NIVEL A: API REST oficial gratuita (POST con keywords + location)."""

    name = "jooble"
    label = "Jooble"
    tier = "A"
    requires_env = ("JOOBLE_API_KEY",)

    def fetch_jobs(self, keywords: list[str], locations: dict[str, Any]) -> list[JobOffer]:
        """Consulta cada combinación keyword x ubicación configurada."""
        self.ensure_credentials()
        api_key = self.env("JOOBLE_API_KEY")
        # Host regional: la key DEBE ser del mismo pais que el host
        host = self.option("host", "jooble.org")
        url = f"https://{host}/api/{api_key}"
        search_locations: list[str] = self.option(
            "locations",
            [locations.get("local_city", ""), locations.get("country", ""), "Remoto"],
        )
        offers: list[JobOffer] = []
        errors: list[str] = []
        for keyword in keywords:
            for location in [loc for loc in search_locations if loc]:
                payload = {"keywords": keyword, "location": location, "page": "1"}
                try:
                    response = self.http.post(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                    )
                    content_type = response.headers.get("Content-Type", "")
                    if "json" not in content_type.lower():
                        raise ScraperError(
                            f"respuesta no-JSON ({content_type[:40]}): posible bloqueo de Cloudflare"
                        )
                    data = response.json()
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)
                    # 403 = la key no corresponde a este host regional
                    if "403" in message:
                        raise ScraperError(
                            f"HTTP 403: la JOOBLE_API_KEY no es valida para el host '{host}'. "
                            "Las keys de Jooble son por pais: registra una en "
                            f"https://{host}/api/about o cambia source_options.jooble.host "
                            "al pais donde creaste la key."
                        ) from exc
                    errors.append(f"{keyword}/{location}: {message}")
                    continue
                for item in (data.get("jobs") or [])[: self.max_offers]:
                    offers.append(
                        self.make_offer(
                            title=item.get("title", ""),
                            company=item.get("company", ""),
                            location=item.get("location", "") or location,
                            salary=item.get("salary", "") or "",
                            url=item.get("link", ""),
                            description=item.get("snippet", ""),
                            posted_at=self.parse_datetime(item.get("updated")),
                        )
                    )
        if not offers and errors:
            raise ScraperError("; ".join(errors[:3]))
        return offers
