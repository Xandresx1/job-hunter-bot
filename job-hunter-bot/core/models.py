"""Modelos de datos del bot."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse


def strip_accents(text: str) -> str:
    """Elimina tildes/diacríticos para comparaciones robustas."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def normalize_text(text: Optional[str]) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    if not text:
        return ""
    clean = strip_accents(str(text)).lower()
    return re.sub(r"\s+", " ", clean).strip()


def clean_html(text: Optional[str]) -> str:
    """Convierte HTML en texto plano legible."""
    if not text:
        return ""
    txt = re.sub(r"<br\s*/?>", "\n", str(text), flags=re.I)
    txt = re.sub(r"</(p|div|li|h\d)>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = (
        txt.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def normalize_url(url: str) -> str:
    """Normaliza una URL para deduplicación (sin query, sin fragmento, sin '/' final)."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", "", ""))
    except ValueError:
        return url.strip().lower()


def keyword_tokens(keyword: str, min_len: int = 3) -> list[str]:
    """Tokens significativos de una keyword ('junior developer' -> ['junior','developer'])."""
    tokens = re.findall(r"[a-z0-9#+.]+", normalize_text(keyword))
    return [t for t in tokens if len(t) >= min_len] or tokens


def matches_keyword(text: str, keyword: str) -> bool:
    """True si TODOS los tokens de la keyword aparecen en el texto normalizado.

    Permite que 'junior developer' haga match con 'Junior Backend Developer'.
    """
    haystack = normalize_text(text)
    tokens = keyword_tokens(keyword)
    if not tokens:
        return False
    return all(token in haystack for token in tokens)


def matches_any_keyword(text: str, keywords: list[str]) -> bool:
    """True si el texto hace match con alguna de las keywords (token a token)."""
    return any(matches_keyword(text, k) for k in keywords if k)


# Términos que NUNCA se recortan del título al calcular la huella de deduplicación:
# distinguen ofertas realmente distintas ("Developer - Backend" vs "Developer - Frontend").
SIGNIFICANT_TAIL_TERMS: frozenset[str] = frozenset(
    {
        "frontend", "front", "backend", "back", "fullstack", "full", "stack", "react",
        "angular", "vue", "java", "python", "php", "node", "nodejs", "javascript", "net",
        "qa", "data", "mobile", "android", "ios", "flutter", "sql", "devops", "cloud",
        "junior", "senior", "trainee", "practicante", "intern", "jr", "sr",
    }
)


def dedup_fingerprint(title: str, company: str) -> str:
    """Huella tolerante de una oferta: título sin sufijos de ubicación + empresa.

    Elimina colas cortas tras un separador ("- Arequipa", "| Remoto", ", Lima") salvo
    que aporten información técnica relevante, y recorta el resultado a 60 caracteres.
    """
    text = normalize_text(title)
    for _ in range(2):
        match = re.search(r"\s[-|\u00b7\u2022,/]\s*([a-z0-9 .]{1,28})$", text)
        if not match:
            break
        tail_words = re.sub(r"[^a-z0-9 ]", " ", match.group(1)).split()
        if not tail_words or len(tail_words) > 3:
            break
        if any(word in SIGNIFICANT_TAIL_TERMS for word in tail_words):
            break
        text = text[: match.start()].strip()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:60]
    company_key = re.sub(r"[^a-z0-9]", "", normalize_text(company))[:18]
    return f"{text}|{company_key}"


@dataclass
class JobOffer:
    """Una oferta de empleo normalizada, independiente de la fuente."""

    title: str
    company: str = ""
    location: str = ""
    salary: str = ""
    url: str = ""
    description: str = ""
    source: str = ""
    score: int = 0
    is_remote: bool = False
    country: str = ""
    posted_at: Optional[datetime] = None
    matched_skills: list[str] = field(default_factory=list)
    reject_reason: str = ""
    score_breakdown: dict[str, int] = field(default_factory=dict, repr=False)
    ai_score: Optional[int] = None
    ai_reason: str = ""
    detail_fetched: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.company = (self.company or "").strip()
        self.location = (self.location or "").strip()
        self.salary = (self.salary or "").strip()
        self.url = (self.url or "").strip()
        self.description = clean_html(self.description)
        if self.posted_at and self.posted_at.tzinfo is None:
            self.posted_at = self.posted_at.replace(tzinfo=timezone.utc)

    @property
    def job_id(self) -> str:
        """SHA-256 de (url normalizada + título + empresa)."""
        base = "|".join(
            [normalize_url(self.url), normalize_text(self.title), normalize_text(self.company)]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @property
    def dedup_key(self) -> str:
        """Huella para deduplicación cruzada entre fuentes (título + empresa).

        Usa una huella tolerante: el título normalizado sin signos y recortado a 45
        caracteres, más la empresa sin espacios. Así la misma oferta publicada como
        "Practicante ... " y "Practicante ... - Arequipa" genera la MISMA clave.
        """
        base = dedup_fingerprint(self.title, self.company)
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @property
    def age_hours(self) -> Optional[float]:
        """Antigüedad de la publicación en horas (None si se desconoce)."""
        if not self.posted_at:
            return None
        now = datetime.now(timezone.utc)
        delta = now - self.posted_at
        return max(delta.total_seconds() / 3600.0, 0.0)

    def summary(self, max_chars: int = 300) -> str:
        """Resumen corto de la descripción para la notificación."""
        text = re.sub(r"\s+", " ", self.description or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rsplit(" ", 1)[0] + "…"

    def searchable_text(self) -> str:
        """Texto normalizado (título + empresa + ubicación + descripción)."""
        return normalize_text(
            " ".join([self.title, self.company, self.location, self.description])
        )

    def to_row(self) -> dict[str, Any]:
        """Diccionario listo para insertar en SQLite."""
        return {
            "id": self.job_id,
            "dedup_key": self.dedup_key,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "salary": self.salary,
            "url": self.url,
            "description": self.description[:8000],
            "source": self.source,
            "score": int(self.score),
            "is_remote": 1 if self.is_remote else 0,
            "country": self.country,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "matched_skills": ", ".join(self.matched_skills),
            "ai_score": self.ai_score,
            "ai_reason": self.ai_reason,
        }
