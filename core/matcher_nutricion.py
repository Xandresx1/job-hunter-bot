"""Matcher independiente para ofertas de NUTRICIONISTA (modo filtro, SIN puntaje).

Reglas (todas configurables en config.yaml -> nutricion_profile):
  1. El título DEBE mencionar nutrición (nutricionista, nutrición, dietista...).
  2. SOLO Arequipa: nunca fuera de la ciudad.
  3. Se descarta si EXIGE SERUMS (salvo que diga explícitamente "sin serums").
  4. La oferta debe pedir egresado/bachiller/titulado/licenciado en nutrición.

Toda oferta que pase los 4 filtros se acepta con score 100 (no hay ranking:
se notifican TODAS las que cumplan).
"""
from __future__ import annotations

from typing import Any

from core.logger import get_logger
from core.matcher import MatchResult, has_term
from core.models import JobOffer, normalize_text


class NutricionMatcher:
    """Filtra ofertas de nutricionista: acepta/rechaza sin ranking de puntaje."""

    def __init__(self, config: dict[str, Any]) -> None:
        profile = config.get("nutricion_profile", {}) or {}
        self.enabled = bool(profile.get("enabled", False))
        self.keywords = [k for k in (profile.get("keywords") or []) if k]
        self.must_have_title = [
            normalize_text(k) for k in (profile.get("must_have_title") or []) if k
        ]
        self.only_city = normalize_text(profile.get("only_city", "arequipa"))
        # SOLO se excluye SERUMS (el sector público ya NO se descarta)
        self.exclude = [normalize_text(k) for k in (profile.get("exclude_keywords") or ["serums", "serum"]) if k]
        # Frases que indican que el SERUMS NO es requisito (no descartar)
        self.exclude_allow = [
            normalize_text(k)
            for k in (
                profile.get("exclude_allow_patterns")
                or [
                    "sin serums",
                    "sin serum",
                    "no requiere serums",
                    "no se requiere serums",
                    "no indispensable serums",
                    "serums no indispensable",
                    "con o sin serums",
                ]
            )
            if k
        ]
        # La oferta debe pedir alguno de estos grados (prefijos: cubren plurales
        # y femeninos: egresad-o/a/os, titulad-o/a, licenciad-o/a, etc.)
        self.require_degree = bool(profile.get("require_degree", True))
        self.degree_terms = [
            normalize_text(k)
            for k in (
                profile.get("degree_terms")
                or ["egresad", "bachiller", "titulad", "licenciad", "colegiad", "titulo profesional", "licenciatura"]
            )
            if k
        ]
        self.log = get_logger("matcher_nutricion")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _phrase_hits(haystack: str, phrases: list[str]) -> list[str]:
        """Frases multi-palabra por subcadena; palabras sueltas por límite de palabra."""
        hits: list[str] = []
        for p in phrases:
            if not p:
                continue
            if " " in p:
                if p in haystack:
                    hits.append(p)
            elif has_term(haystack, [p]):
                hits.append(p)
        return hits

    @staticmethod
    def _prefix_hits(haystack: str, prefixes: list[str]) -> list[str]:
        """Coincidencia por subcadena (prefijos tipo 'titulad' -> titulado/a/os)."""
        return [p for p in prefixes if p and p in haystack]

    # ------------------------------------------------------------------ filtro
    def evaluate_offer(self, offer: JobOffer) -> MatchResult:
        """Devuelve score 100 si pasa TODOS los filtros; 0 con el motivo si no."""
        title = normalize_text(offer.title)
        full_text = offer.searchable_text()

        # 1) El TÍTULO debe mencionar nutrición
        title_hits = self._phrase_hits(title, self.must_have_title)
        if not title_hits:
            return MatchResult(0, [], "titulo sin mencion a nutricion")

        # 2) SOLO Arequipa
        loc_text = normalize_text(
            f"{offer.location} {offer.title} {offer.description[:600]}"
        )
        if self.only_city not in loc_text:
            return MatchResult(0, [], f"fuera de {self.only_city}")

        # 3) Descartar SOLO si exige SERUMS (con excepción de "sin serums")
        excl = self._phrase_hits(full_text, self.exclude)
        if excl and not any(allow in full_text for allow in self.exclude_allow):
            return MatchResult(0, [], f"exige serums: '{excl[0]}'")

        # 4) Debe pedir egresado / bachiller / titulado / licenciado
        degree_hits = self._prefix_hits(full_text, self.degree_terms)
        if self.require_degree and not degree_hits:
            return MatchResult(0, [], "no menciona egresado/bachiller/titulado")

        matched = list(dict.fromkeys(title_hits + degree_hits + [self.only_city]))
        return MatchResult(100, matched, "", {"filtro_nutricion": 100})

    def evaluate(self, offers: list[JobOffer]) -> list[JobOffer]:
        """Devuelve TODAS las ofertas que pasan los filtros (sin ranking)."""
        if not self.enabled:
            return []
        accepted: list[JobOffer] = []
        for offer in offers:
            if not offer.title or not offer.url:
                continue
            try:
                result = self.evaluate_offer(offer)
            except Exception as exc:  # noqa: BLE001 - nunca romper el ciclo
                self.log.warning("Error evaluando '%s': %s", offer.title[:60], exc)
                continue
            if result.score > 0:
                offer.score = result.score
                offer.matched_skills = result.matched_skills
                offer.reject_reason = ""
                offer.score_breakdown = result.breakdown
                accepted.append(offer)
        return accepted
