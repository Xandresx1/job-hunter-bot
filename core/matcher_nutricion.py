"""Matcher independiente para ofertas de NUTRICIONISTA.

Reglas:
  - El título DEBE mencionar nutrición (nutricionista, nutrición, dietista...).
  - SOLO Arequipa: nunca fuera de la ciudad, nunca remoto.
  - Se descartan ofertas del sector público o que pidan SERUMS.
  - Bonus si la oferta es de clínica privada / centro médico / consultorio.

Se configura por completo en config.yaml -> nutricion_profile.
"""
from __future__ import annotations

from typing import Any

from core.logger import get_logger
from core.matcher import MatchResult, has_term
from core.models import JobOffer, normalize_text


class NutricionMatcher:
    """Puntúa ofertas de nutricionista 0-100 según nutricion_profile."""

    def __init__(self, config: dict[str, Any]) -> None:
        profile = config.get("nutricion_profile", {}) or {}
        self.enabled = bool(profile.get("enabled", False))
        self.min_score = int(profile.get("min_score", 50))
        self.keywords = [k for k in (profile.get("keywords") or []) if k]
        self.must_have_title = [normalize_text(k) for k in (profile.get("must_have_title") or []) if k]
        self.only_city = normalize_text(profile.get("only_city", "arequipa"))
        self.private_signals = [normalize_text(k) for k in (profile.get("private_clinic_signals") or []) if k]
        self.exclude = [normalize_text(k) for k in (profile.get("exclude_keywords") or []) if k]
        self.friendly = [normalize_text(k) for k in (profile.get("friendly_signals") or []) if k]
        self.fresh_hours = float(profile.get("fresh_hours", 48))
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

    # ------------------------------------------------------------------ scoring
    def evaluate_offer(self, offer: JobOffer) -> MatchResult:
        title = normalize_text(offer.title)
        full_text = offer.searchable_text()

        # 1) El TÍTULO debe mencionar nutrición
        title_hits = self._phrase_hits(title, self.must_have_title)
        if not title_hits:
            return MatchResult(0, [], "titulo sin mencion a nutricion")

        # 2) SOLO Arequipa (ni remoto, ni otras ciudades)
        loc_text = normalize_text(
            f"{offer.location} {offer.title} {offer.description[:600]}"
        )
        if self.only_city not in loc_text:
            return MatchResult(0, [], f"fuera de {self.only_city}")

        # 3) Descartar sector público / SERUMS
        excl = self._phrase_hits(full_text, self.exclude)
        if excl:
            return MatchResult(0, [], f"excluida (sector publico/serums): '{excl[0]}'")

        # 4) Scoring positivo
        breakdown: dict[str, int] = {"titulo_nutricion": 30, "arequipa": 20}
        total = 50
        matched: list[str] = list(title_hits)

        clinic_hits = self._phrase_hits(full_text, self.private_signals)
        if clinic_hits:
            breakdown["clinica_privada"] = 25
            total += 25
            matched.extend(clinic_hits[:3])

        friendly_hits = self._phrase_hits(full_text, self.friendly)
        if friendly_hits:
            breakdown["senales_amigables"] = 10
            total += 10

        age = offer.age_hours
        if age is not None and age <= self.fresh_hours:
            breakdown["publicacion_reciente"] = 10
            total += 10

        return MatchResult(min(total, 100), list(dict.fromkeys(matched)), "", breakdown)

    def evaluate(self, offers: list[JobOffer]) -> list[JobOffer]:
        """Puntúa la lista y devuelve solo las aceptadas (no toca las rechazadas)."""
        if not self.enabled:
            return []
        accepted: list[JobOffer] = []
        for offer in offers:
            if not offer.title or not offer.url:
                continue
            try:
                result = self.evaluate_offer(offer)
            except Exception as exc:  # noqa: BLE001 - nunca romper el ciclo
                self.log.warning("Error puntuando '%s': %s", offer.title[:60], exc)
                continue
            if result.score >= self.min_score:
                offer.score = result.score
                offer.matched_skills = result.matched_skills
                offer.reject_reason = ""
                offer.score_breakdown = result.breakdown
                accepted.append(offer)
        accepted.sort(key=lambda o: o.score, reverse=True)
        return accepted
