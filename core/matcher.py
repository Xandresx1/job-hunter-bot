"""Sistema de matching basado en el CV del usuario (`cv_profile` de config.yaml).

Dos etapas:
  ETAPA 1 - filtros duros: seniority, años de experiencia, inglés avanzado,
            stack incompatible y rubro no relacionado a desarrollo -> score 0.
  ETAPA 2 - scoring positivo 0-100 según el perfil real del CV.

Nada está hardcodeado: todas las listas (skills, stacks, patrones de inglés,
rubros no relacionados, señales amigables) provienen de config.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from core.logger import get_logger
from core.models import JobOffer, matches_any_keyword, normalize_text

REMOTE_HINTS: tuple[str, ...] = (
    "remoto",
    "remote",
    "teletrabajo",
    "home office",
    "trabajo desde casa",
    "work from home",
    "anywhere",
    "wfh",
)

INTERNATIONAL_REMOTE_HINTS: tuple[str, ...] = (
    "worldwide",
    "anywhere",
    "latam",
    "latin america",
    "americas",
    "global",
    "remote international",
    "remoto internacional",
    "any country",
    "emea",
)

# "3+ años de experiencia", "minimo 4 anos", "5+ years of experience", "2 to 4 years"
YEARS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\d{1,2})\s*(?:\+|o mas|or more)?\s*(?:anos|ano|years|year|yrs)\b"),
    re.compile(r"(?:minimo|min\.?|al menos|at least|minimum)\s*(?:de\s*)?(\d{1,2})\s*(?:anos|years)?"),
    re.compile(r"(?:experiencia|experience)\D{0,25}?(\d{1,2})\s*(?:\+)?\s*(?:anos|years)"),
)
EXPERIENCE_CONTEXT: tuple[str, ...] = ("experien", "exp.", "trayectoria", "seniority")

_TERM_CACHE: dict[str, re.Pattern[str]] = {}


def term_pattern(term: str) -> re.Pattern[str]:
    """Compila (y cachea) un patrón con límites de palabra para un término.

    Evita falsos positivos como 'ia' dentro de 'experiencia' o 'java' dentro de
    'javascript', respetando términos con símbolos ('c++', 'c#', 'node.js', '.net').
    """
    pattern = _TERM_CACHE.get(term)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")
        _TERM_CACHE[term] = pattern
    return pattern


def find_terms(text: str, terms: Iterable[str]) -> list[str]:
    """Devuelve los términos presentes en el texto usando límites de palabra."""
    norm = normalize_text(text)
    found = [t for t in terms if t and term_pattern(t).search(norm)]
    # Elimina alias contenidos en otro término ya encontrado ('js' si hay 'javascript')
    return [t for t in found if not any(t != o and t in o for o in found)]


def has_term(text: str, terms: Iterable[str]) -> bool:
    """True si alguno de los términos aparece como palabra completa."""
    norm = normalize_text(text)
    return any(t and term_pattern(t).search(norm) for t in terms)


@dataclass
class MatchResult:
    """Resultado del matching de una oferta contra el CV."""

    score: int = 0
    matched_skills: list[str] = field(default_factory=list)
    reject_reason: str = ""
    breakdown: dict[str, int] = field(default_factory=dict)


class Matcher:
    """Puntúa ofertas 0-100 comparando con el perfil del CV configurado."""

    def __init__(self, config: dict[str, Any]) -> None:
        search = config.get("search", {}) or {}
        matching = config.get("matching", {}) or {}
        locations = search.get("locations", {}) or {}
        cv = config.get("cv_profile", {}) or {}

        self.cv_summary: str = str(cv.get("summary", "") or "")
        self.keywords = [normalize_text(k) for k in (search.get("keywords") or []) if k]
        self.exclude_keywords = [normalize_text(k) for k in (search.get("exclude_keywords") or []) if k]

        self.seniority = [normalize_text(k) for k in (cv.get("seniority") or []) if k]
        self.core_skills = [normalize_text(k) for k in (cv.get("core_skills") or []) if k]
        self.secondary_skills = [normalize_text(k) for k in (cv.get("secondary_skills") or []) if k]
        self.incompatible_stacks = [normalize_text(k) for k in (cv.get("incompatible_stacks") or []) if k]
        self.dev_roles = [normalize_text(k) for k in (cv.get("dev_role_keywords") or []) if k]
        self.non_dev = [normalize_text(k) for k in (cv.get("non_dev_keywords") or []) if k]
        self.friendly_signals = [normalize_text(k) for k in (cv.get("friendly_signals") or []) if k]
        self.english_exclude = [normalize_text(k) for k in (cv.get("english_exclude_patterns") or []) if k]
        self.english_allow = [normalize_text(k) for k in (cv.get("english_allow_patterns") or []) if k]
        self.max_years = int(cv.get("max_years_experience", 2))
        self.strict_english = bool(cv.get("strict_english_filter", True))

        self.local_city = normalize_text(locations.get("local_city", ""))
        self.country = normalize_text(locations.get("country", ""))
        self.extra_cities = [normalize_text(c) for c in (locations.get("extra_local_cities") or []) if c]
        self.accept_remote = bool(locations.get("accept_remote", True))
        self.accept_remote_international = bool(locations.get("accept_remote_international", True))
        self.remote_only_outside_local = bool(locations.get("remote_only_outside_local", False))
        self.deprioritize_keywords = [normalize_text(k) for k in (cv.get("deprioritize_keywords") or []) if k]
        self.deprioritize_penalty = int(cv.get("deprioritize_penalty", 15))
        self.target_countries = [normalize_text(c) for c in (locations.get("target_countries") or []) if c]

        self.min_score = int(matching.get("min_score", 55))
        self.fresh_hours = float(matching.get("fresh_hours", 48))
        self.log = get_logger("matcher")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
        """Coincidencia por palabra completa (no subcadena)."""
        return has_term(haystack, needles)

    @staticmethod
    def _contains_phrase(haystack: str, phrases: Iterable[str]) -> bool:
        """Coincidencia por subcadena, para frases largas (p. ej. 'ingles avanzado')."""
        return any(p and p in haystack for p in phrases)

    @staticmethod
    def _found(haystack: str, needles: Iterable[str]) -> list[str]:
        """Términos encontrados por palabra completa, sin alias redundantes."""
        return find_terms(haystack, needles)

    @staticmethod
    def _found_phrases(haystack: str, phrases: Iterable[str]) -> list[str]:
        return [p for p in phrases if p and p in haystack]

    def detect_remote(self, offer: JobOffer) -> bool:
        """Determina si la oferta es remota/híbrida."""
        if offer.is_remote:
            return True
        text = normalize_text(f"{offer.location} {offer.title}")
        if self._contains_phrase(text, REMOTE_HINTS):
            return True
        head = normalize_text(offer.description[:700])
        return self._contains_phrase(
            head, ("100% remoto", "fully remote", "trabajo remoto", "remote position", "teletrabajo")
        )

    def is_local(self, offer: JobOffer) -> bool:
        """True si la oferta es en la ciudad local configurada."""
        text = normalize_text(f"{offer.location} {offer.title} {offer.description[:400]}")
        cities = [c for c in [self.local_city, *self.extra_cities] if c]
        return self._contains_phrase(text, cities)

    def is_in_country(self, offer: JobOffer) -> bool:
        """True si la oferta parece estar en el país configurado."""
        text = normalize_text(f"{offer.location} {offer.country}")
        return bool(self.country and self.country in text) or "peru" in text

    def is_international_remote(self, offer: JobOffer) -> bool:
        """True si es remota y abierta a LATAM/worldwide u otro país objetivo."""
        if not self.detect_remote(offer):
            return False
        text = normalize_text(f"{offer.location} {offer.country} {offer.description[:900]}")
        if self._contains_phrase(text, INTERNATIONAL_REMOTE_HINTS):
            return True
        for country in self.target_countries:
            if country and country != self.country and country in text:
                return True
        return False

    def location_accepted(self, offer: JobOffer) -> bool:
        """Filtro de ubicación según config."""
        if self.is_local(offer):
            return True
        # NUEVO: fuera de la ciudad local (Arequipa) SOLO se aceptan ofertas
        # que mencionen explícitamente remoto. Nunca presenciales de otras ciudades.
        if self.remote_only_outside_local:
            return self.detect_remote(offer) and (
                self.accept_remote or self.accept_remote_international
            )
        remote = self.detect_remote(offer)
        if remote and (self.accept_remote or self.accept_remote_international):
            return True
        if self.is_in_country(offer):
            return True
        text = normalize_text(f"{offer.location} {offer.country}")
        return self._contains_phrase(text, self.target_countries)

    # ------------------------------------------------------- ETAPA 1: filtros
    def required_years(self, text: str) -> Optional[int]:
        """Años de experiencia exigidos por la oferta (None si no se detecta)."""
        norm = normalize_text(text)
        found: list[int] = []
        for pattern in YEARS_PATTERNS:
            for match in pattern.finditer(norm):
                try:
                    years = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if years <= 0 or years > 30:
                    continue
                window = norm[max(0, match.start() - 70) : match.end() + 70]
                if not any(ctx in window for ctx in EXPERIENCE_CONTEXT):
                    continue
                found.append(years)
        return min(found) if found else None

    def requires_advanced_english(self, text: str) -> bool:
        """True si la oferta exige inglés avanzado/fluido/bilingue."""
        if not self.strict_english:
            return False
        norm = normalize_text(text)
        hits = self._found_phrases(norm, self.english_exclude)
        if not hits:
            return False
        # Si también dice explícitamente "ingles basico/intermedio", no descartar
        allow_hits = self._found_phrases(norm, self.english_allow)
        if allow_hits and len(allow_hits) >= len(hits):
            return False
        return True

    def stack_incompatible(self, text: str, matched_skills: list[str]) -> Optional[str]:
        """Devuelve el stack incompatible si la oferta gira solo alrededor de él."""
        if matched_skills:
            return None
        norm = normalize_text(text)
        hits = self._found(norm, self.incompatible_stacks)
        return hits[0] if hits else None

    def is_non_dev(self, title: str, text: str, matched_skills: list[str]) -> bool:
        """True si la oferta no es de desarrollo de software."""
        norm_title = normalize_text(title)
        norm_all = normalize_text(text)
        role_hit = self._contains_any(norm_title, self.dev_roles) or self._contains_any(
            norm_all[:1500], self.dev_roles
        )
        if role_hit or matched_skills:
            # Si el título es claramente de otro rubro y no hay rol técnico en el título
            if (
                self._contains_any(norm_title, self.non_dev)
                and not self._contains_any(norm_title, self.dev_roles)
                and not matched_skills
            ):
                return True
            return False
        return True

    # ------------------------------------------------------- ETAPA 2: scoring
    def skills_score(self, text: str) -> tuple[int, list[str]]:
        """Puntaje por skills del CV (core 6 pts máx 24, secundarias 2 pts máx 6)."""
        norm = normalize_text(text)
        core_hits = self._found(norm, self.core_skills)
        secondary_hits = self._found(norm, self.secondary_skills)
        core_points = min(len(core_hits) * 6, 24)
        secondary_points = min(len(secondary_hits) * 2, 6)
        matched = list(dict.fromkeys(core_hits + secondary_hits))
        return core_points + secondary_points, matched

    def evaluate_offer(self, offer: JobOffer) -> MatchResult:
        """Aplica filtros duros + scoring y devuelve el resultado detallado."""
        title = normalize_text(offer.title)
        full_text = offer.searchable_text()

        # --- ETAPA 1: filtros duros ---
        exclusion = self._found(title, self.exclude_keywords) or self._found(
            full_text[:600], self.exclude_keywords
        )
        if exclusion:
            return MatchResult(0, [], f"seniority excluyente: '{exclusion[0]}'")

        years = self.required_years(full_text)
        if years is not None and years > self.max_years:
            return MatchResult(0, [], f"exige {years} anos de experiencia (max {self.max_years})")

        if self.requires_advanced_english(full_text):
            return MatchResult(0, [], "exige ingles avanzado/fluido")

        skill_points, matched_skills = self.skills_score(f"{offer.title} {offer.description}")

        incompatible = self.stack_incompatible(full_text, matched_skills)
        if incompatible:
            return MatchResult(0, [], f"stack incompatible: '{incompatible}'")

        if self.is_non_dev(offer.title, full_text, matched_skills):
            return MatchResult(0, [], "rubro no relacionado a desarrollo de software")

        if not self.location_accepted(offer):
            return MatchResult(0, matched_skills, "ubicacion no aceptada")

        title_is_junior = self._contains_any(title, self.seniority) or matches_any_keyword(
            title, self.keywords
        )
        relevant = title_is_junior or matches_any_keyword(full_text, self.keywords)
        if not relevant:
            return MatchResult(0, matched_skills, "no coincide con las keywords configuradas")

        # --- ETAPA 2: scoring positivo ---
        breakdown: dict[str, int] = {}
        total = 0
        if title_is_junior:
            breakdown["titulo_junior"] = 25
            total += 25
        if skill_points:
            breakdown["skills_cv"] = skill_points
            total += skill_points

        is_remote = self.detect_remote(offer)
        if self.is_local(offer) or (is_remote and self.is_in_country(offer)) or (
            is_remote and self.accept_remote and not self.is_international_remote(offer)
        ):
            breakdown["arequipa_o_remoto_peru"] = 15
            total += 15
        if self.accept_remote_international and self.is_international_remote(offer):
            breakdown["remoto_internacional"] = 10
            total += 10

        age = offer.age_hours
        if age is not None and age <= self.fresh_hours:
            breakdown["publicacion_reciente"] = 10
            total += 10

        if self._contains_any(full_text, self.friendly_signals):
            breakdown["senales_amigables"] = 10
            total += 10

        if self.deprioritize_keywords and (
            self._contains_any(title, self.deprioritize_keywords)
            or self._contains_phrase(title, self.deprioritize_keywords)
        ):
            breakdown["menos_desarrollo_web"] = -self.deprioritize_penalty
            total -= self.deprioritize_penalty

        offer.is_remote = is_remote
        return MatchResult(max(min(total, 100), 0), matched_skills, "", breakdown)

    # Compatibilidad: score simple
    def score(self, offer: JobOffer) -> int:
        """Devuelve solo el score numérico (0 = descartada)."""
        return self.evaluate_offer(offer).score

    def evaluate(self, offers: list[JobOffer]) -> list[JobOffer]:
        """Puntúa una lista y devuelve las ofertas que superan el umbral."""
        accepted: list[JobOffer] = []
        for offer in offers:
            if not offer.title or not offer.url:
                continue
            try:
                result = self.evaluate_offer(offer)
            except Exception as exc:  # noqa: BLE001 - nunca romper el ciclo
                self.log.warning("Error puntuando '%s': %s", offer.title[:60], exc)
                continue
            offer.score = result.score
            offer.matched_skills = result.matched_skills
            offer.reject_reason = result.reject_reason
            offer.score_breakdown = result.breakdown
            if result.score >= self.min_score:
                accepted.append(offer)
        accepted.sort(key=lambda o: o.score, reverse=True)
        return accepted
