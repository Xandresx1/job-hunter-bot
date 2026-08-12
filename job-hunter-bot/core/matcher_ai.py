"""ETAPA 3 (opcional): matching semántico CV vs oferta con un LLM.

Desactivado por defecto (`matching.use_ai_matching: false`). Si se activa y hay
credenciales, cada oferta candidata se envía a un modelo barato pidiendo un JSON
`{"match_score": 0-100, "reason": "..."}`.

Proveedores soportados:
  * ``openai``   -> API OpenAI (o cualquier endpoint compatible) con OPENAI_API_KEY.
                   Base URL configurable con OPENAI_BASE_URL (default api.openai.com).
  * ``emergent`` -> librería emergentintegrations con EMERGENT_LLM_KEY.

El módulo nunca lanza excepciones hacia el ciclo: si algo falla se registra el
error y el bot usa únicamente el score de reglas. Los resultados se cachean en
SQLite para no re-evaluar la misma oferta.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

from core.logger import get_logger
from core.models import JobOffer

SYSTEM_PROMPT = (
    "Eres un reclutador tecnico. Comparas el CV de un candidato junior con una oferta "
    "de empleo y evaluas que tan realista es que lo contraten. Responde SIEMPRE y "
    "UNICAMENTE con un JSON valido con las claves match_score (entero 0-100) y reason "
    "(una frase corta en espanol, maximo 200 caracteres). Penaliza fuerte si la oferta "
    "pide mas experiencia de la que tiene, ingles avanzado, o un stack que no maneja."
)


class AIMatcher:
    """Evaluador semántico opcional basado en LLM."""

    def __init__(self, config: dict[str, Any], database: Any = None) -> None:
        matching = config.get("matching", {}) or {}
        cv = config.get("cv_profile", {}) or {}
        self.enabled = bool(matching.get("use_ai_matching", False))
        self.provider = str(matching.get("ai_provider", "openai")).lower()
        self.model = str(matching.get("ai_model", "gpt-4o-mini"))
        self.weight = float(matching.get("ai_weight", 0.4))
        self.min_rule_score = int(matching.get("ai_min_rule_score", 40))
        self.max_calls = int(matching.get("ai_max_calls_per_cycle", 25))
        self.timeout = int(matching.get("ai_timeout_seconds", 25))
        self.cv_summary = str(cv.get("summary", "") or "").strip()
        self.database = database
        self.log = get_logger("matcher_ai")
        self.calls_made = 0

    # ------------------------------------------------------------- utilidades
    @property
    def api_key(self) -> str:
        """Credencial según el proveedor configurado."""
        if self.provider == "emergent":
            return (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
        return (os.environ.get("OPENAI_API_KEY") or "").strip()

    @property
    def available(self) -> bool:
        """True si el matching con IA puede ejecutarse."""
        if not self.enabled:
            return False
        if not self.api_key:
            self.log.warning(
                "use_ai_matching activo pero falta la credencial (%s); se usan solo reglas",
                "EMERGENT_LLM_KEY" if self.provider == "emergent" else "OPENAI_API_KEY",
            )
            return False
        if not self.cv_summary:
            self.log.warning("cv_profile.summary vacio: se omite el matching con IA")
            return False
        return True

    def _prompt(self, offer: JobOffer) -> str:
        """Construye el prompt de comparación CV vs oferta."""
        description = re.sub(r"\s+", " ", offer.description or "")[:2500]
        return (
            f"CV DEL CANDIDATO:\n{self.cv_summary}\n\n"
            f"OFERTA:\nTitulo: {offer.title}\nEmpresa: {offer.company}\n"
            f"Ubicacion: {offer.location}\nDescripcion: {description}\n\n"
            'Devuelve solo JSON: {"match_score": <0-100>, "reason": "<motivo breve>"}'
        )

    @staticmethod
    def _parse(content: str) -> Optional[tuple[int, str]]:
        """Extrae (score, reason) de la respuesta del modelo."""
        if not content:
            return None
        text = content.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        try:
            score = int(round(float(data.get("match_score", 0))))
        except (TypeError, ValueError):
            return None
        score = max(0, min(100, score))
        return score, str(data.get("reason", ""))[:200]

    # -------------------------------------------------------------- backends
    def _call_openai(self, prompt: str) -> Optional[str]:
        """Llama a un endpoint compatible con OpenAI Chat Completions."""
        base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
            timeout=self.timeout,
        )
        if response.status_code == 400:
            # Algunos modelos/endpoints no aceptan response_format: reintenta sin él
            payload.pop("response_format", None)
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload).encode("utf-8"),
                timeout=self.timeout,
            )
        response.raise_for_status()
        data = response.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")

    def _call_emergent(self, prompt: str) -> Optional[str]:
        """Llama al LLM a través de la librería emergentintegrations (opcional)."""
        try:
            import asyncio

            from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "emergentintegrations no está instalado. Usa ai_provider: openai o instala "
                "la librería con: pip install emergentintegrations "
                "--extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/"
            ) from exc

        chat = LlmChat(
            api_key=self.api_key,
            session_id="job-hunter-matcher",
            system_message=SYSTEM_PROMPT,
        ).with_model("openai", self.model)

        async def _run() -> str:
            response = await chat.send_message(UserMessage(text=prompt))
            return str(response)

        return asyncio.run(_run())

    # ------------------------------------------------------------------- API
    def evaluate(self, offer: JobOffer, rule_score: int) -> tuple[int, str]:
        """Devuelve (score_final, motivo). Si la IA no aplica, retorna el score de reglas.

        El score final es: (1 - weight) * reglas + weight * IA.
        """
        if not self.available or rule_score < self.min_rule_score:
            return rule_score, ""
        if self.calls_made >= self.max_calls:
            return rule_score, ""

        cached = None
        if self.database is not None:
            cached = self.database.get_ai_cache(offer.job_id)
        if cached:
            ai_score, reason = cached
        else:
            try:
                content = (
                    self._call_emergent(self._prompt(offer))
                    if self.provider == "emergent"
                    else self._call_openai(self._prompt(offer))
                )
            except Exception as exc:  # noqa: BLE001 - degradación elegante
                self.log.warning("IA no disponible (%s): se usan solo reglas", str(exc)[:160])
                return rule_score, ""
            self.calls_made += 1
            parsed = self._parse(content or "")
            if not parsed:
                self.log.warning("Respuesta de IA no parseable para '%s'", offer.title[:50])
                return rule_score, ""
            ai_score, reason = parsed
            if self.database is not None:
                self.database.save_ai_cache(offer.job_id, ai_score, reason, self.model)

        final = int(round(rule_score * (1 - self.weight) + ai_score * self.weight))
        offer.ai_score = ai_score
        offer.ai_reason = reason
        self.log.info(
            "IA: '%s' reglas=%s ia=%s final=%s (%s)",
            offer.title[:45],
            rule_score,
            ai_score,
            final,
            reason[:80],
        )
        return max(0, min(100, final)), reason
