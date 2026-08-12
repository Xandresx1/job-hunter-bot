"""Clase base común para todos los scrapers/APIs de empleo."""
from __future__ import annotations

import abc
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bs4 import BeautifulSoup

from core.http_client import HttpClient, playwright_get
from core.logger import get_logger
from core.models import JobOffer, normalize_text

COUNTRY_ALIASES: dict[str, str] = {
    "peru": "Perú",
    "lima": "Perú",
    "arequipa": "Perú",
    "spain": "España",
    "espana": "España",
    "madrid": "España",
    "barcelona": "España",
    "chile": "Chile",
    "santiago": "Chile",
    "argentina": "Argentina",
    "buenos aires": "Argentina",
    "mexico": "México",
    "cdmx": "México",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "us": "Estados Unidos",
    "new zealand": "Nueva Zelanda",
    "finland": "Finlandia",
    "helsinki": "Finlandia",
    "canada": "Canadá",
    "germany": "Alemania",
    "deutschland": "Alemania",
    "berlin": "Alemania",
    "munich": "Alemania",
    "colombia": "Colombia",
    "uruguay": "Uruguay",
    "brazil": "Brasil",
    "brasil": "Brasil",
    "worldwide": "Remote",
    "anywhere": "Remote",
    "latam": "Remote",
    "remote": "Remote",
}

MONTHS_ES: dict[str, int] = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


class ScraperError(Exception):
    """Fallo controlado de una fuente (bloqueo, cambio de HTML, timeout...)."""


class SkipSource(ScraperError):
    """La fuente se omite (por ejemplo: falta la API key)."""


class BaseScraper(abc.ABC):
    """Interfaz común: `fetch_jobs(keywords, locations) -> list[JobOffer]`."""

    name: str = "base"
    label: str = "Base"
    tier: str = "A"  # A = API, B = HTML, C = anti-bot fuerte
    requires_env: tuple[str, ...] = ()

    def __init__(self, http: HttpClient, config: dict[str, Any]) -> None:
        self.http = http
        self.config = config
        self.advanced: dict[str, Any] = config.get("advanced", {}) or {}
        self.options: dict[str, Any] = (
            (config.get("source_options") or {}).get(self.name) or {}
        )
        self.log = get_logger(self.name)

    # ------------------------------------------------------------------ env
    @staticmethod
    def env(key: str, default: str = "") -> str:
        """Lee una variable de entorno (.env) sin lanzar excepciones."""
        return (os.environ.get(key) or default).strip()

    def missing_env(self) -> list[str]:
        """Variables de entorno requeridas que faltan."""
        return [key for key in self.requires_env if not self.env(key)]

    def ensure_credentials(self) -> None:
        """Lanza SkipSource si falta alguna credencial requerida."""
        missing = self.missing_env()
        if missing:
            raise SkipSource(f"faltan credenciales: {', '.join(missing)}")

    # ---------------------------------------------------------------- config
    @property
    def max_offers(self) -> int:
        """Límite de ofertas crudas por ciclo para esta fuente."""
        return int(self.advanced.get("max_offers_per_source", 300))

    @property
    def use_playwright(self) -> bool:
        """True si el modo Playwright está habilitado en config."""
        return bool(self.advanced.get("use_playwright", False))

    def option(self, key: str, default: Any = None) -> Any:
        """Lee una opción específica de la fuente desde `source_options`."""
        return self.options.get(key, default)

    # ------------------------------------------------------------- interface
    @abc.abstractmethod
    def fetch_jobs(
        self, keywords: list[str], locations: dict[str, Any]
    ) -> list[JobOffer]:
        """Devuelve las ofertas crudas encontradas para las keywords dadas."""

    # ------------------------------------------------------------- utilidades
    def make_offer(self, **kwargs: Any) -> JobOffer:
        """Construye un JobOffer asignando la fuente actual."""
        kwargs.setdefault("source", self.name)
        location = kwargs.get("location") or ""
        if not kwargs.get("country"):
            kwargs["country"] = self.guess_country(location)
        if not kwargs.get("is_remote"):
            kwargs["is_remote"] = self.looks_remote(f"{location} {kwargs.get('title', '')}")
        return JobOffer(**kwargs)

    @staticmethod
    def looks_remote(text: str) -> bool:
        """Heurística rápida de trabajo remoto sobre un texto."""
        norm = normalize_text(text)
        return any(
            hint in norm
            for hint in ("remot", "teletrabajo", "home office", "anywhere", "worldwide", "wfh")
        )

    @staticmethod
    def guess_country(text: str) -> str:
        """Deduce el país a partir de una cadena de ubicación."""
        norm = normalize_text(text)
        if not norm:
            return ""
        for alias, country in COUNTRY_ALIASES.items():
            if alias in norm:
                return country
        parts = [p.strip() for p in norm.split(",") if p.strip()]
        return parts[-1].title() if parts else ""

    @staticmethod
    def parse_datetime(value: Any) -> Optional[datetime]:
        """Parsea fechas ISO, epoch o textos relativos en español/inglés."""
        if value in (None, "", 0):
            return None
        # epoch
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            try:
                epoch = float(value)
                if epoch > 1_000_000_000_000:  # milisegundos
                    epoch /= 1000.0
                return datetime.fromtimestamp(epoch, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        text = str(value).strip()
        iso = text.replace("Z", "+00:00")
        for candidate in (iso, iso.split(".")[0], text[:10]):
            try:
                parsed = datetime.fromisoformat(candidate)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return BaseScraper.parse_relative_date(text)

    @staticmethod
    def parse_relative_date(text: str) -> Optional[datetime]:
        """Interpreta 'Hace 7 horas', 'hace 2 días', '3 days ago', '03 ago'."""
        norm = normalize_text(text)
        now = datetime.now(timezone.utc)
        if not norm:
            return None
        if any(word in norm for word in ("hoy", "today", "ahora", "just posted", "reciente")):
            return now
        if "ayer" in norm or "yesterday" in norm:
            return now - timedelta(days=1)
        match = re.search(r"(\d+)\s*(minuto|minute|hora|hour|dia|day|semana|week|mes|month)", norm)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith(("minuto", "minute")):
                return now - timedelta(minutes=amount)
            if unit.startswith(("hora", "hour")):
                return now - timedelta(hours=amount)
            if unit.startswith(("dia", "day")):
                return now - timedelta(days=amount)
            if unit.startswith(("semana", "week")):
                return now - timedelta(weeks=amount)
            return now - timedelta(days=30 * amount)
        match = re.search(r"(\d{1,2})\s*([a-z]{3})", norm)
        if match and match.group(2)[:3] in MONTHS_ES:
            day = int(match.group(1))
            month = MONTHS_ES[match.group(2)[:3]]
            year = now.year if month <= now.month else now.year - 1
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    # -------------------------------------------------------------- fetching
    def soup(self, url: str, wait_selector: Optional[str] = None, **kwargs: Any) -> BeautifulSoup:
        """Obtiene un BeautifulSoup usando requests o Playwright si está activado."""
        if self.use_playwright:
            try:
                html = playwright_get(
                    url,
                    timeout=int(self.advanced.get("request_timeout", 20)) + 10,
                    wait_selector=wait_selector,
                )
                return BeautifulSoup(html, "html.parser")
            except RuntimeError as exc:
                self.log.warning("Playwright no disponible (%s); usando requests", exc)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("Playwright falló en %s: %s; usando requests", url, exc)
        return self.http.get_soup(url, **kwargs)

    def offer_from_json_ld(self, block: dict[str, Any], url: str) -> Optional[JobOffer]:
        """Convierte un schema.org/JobPosting en JobOffer."""
        if not block:
            return None
        title = block.get("title") or block.get("name") or ""
        org = block.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else str(org)
        location = self._location_from_json_ld(block)
        salary = self._salary_from_json_ld(block)
        remote = str(block.get("jobLocationType", "")).upper() == "TELECOMMUTE"
        return self.make_offer(
            title=str(title),
            company=str(company),
            location=location,
            salary=salary,
            url=block.get("url") or url,
            description=str(block.get("description") or ""),
            is_remote=remote or self.looks_remote(f"{location} {title}"),
            posted_at=self.parse_datetime(block.get("datePosted")),
            raw={"json_ld": True},
        )

    @staticmethod
    def _location_from_json_ld(block: dict[str, Any]) -> str:
        """Extrae una ubicación legible desde JobPosting.jobLocation."""
        job_location = block.get("jobLocation")
        entries = job_location if isinstance(job_location, list) else [job_location]
        parts: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            address = entry.get("address") or {}
            if isinstance(address, str):
                parts.append(address)
                continue
            chunk = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            joined = ", ".join(str(c) for c in chunk if c)
            if joined:
                parts.append(joined)
        if not parts and str(block.get("jobLocationType", "")).upper() == "TELECOMMUTE":
            applicant = block.get("applicantLocationRequirements") or {}
            if isinstance(applicant, dict) and applicant.get("name"):
                return f"Remoto ({applicant['name']})"
            return "Remoto"
        return " | ".join(dict.fromkeys(parts))

    @staticmethod
    def _salary_from_json_ld(block: dict[str, Any]) -> str:
        """Extrae el salario si viene declarado en el JSON-LD."""
        salary = block.get("baseSalary") or {}
        if not isinstance(salary, dict):
            return ""
        value = salary.get("value") or {}
        currency = salary.get("currency") or salary.get("salaryCurrency") or ""
        if isinstance(value, dict):
            amount = value.get("value") or value.get("minValue")
            maximum = value.get("maxValue")
            unit = value.get("unitText", "")
            if amount and maximum and str(amount) != str(maximum):
                return f"{currency} {amount} - {maximum} {unit}".strip()
            if amount:
                return f"{currency} {amount} {unit}".strip()
        return ""
