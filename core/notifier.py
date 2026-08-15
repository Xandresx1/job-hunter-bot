"""Notificaciones push vía ntfy.sh."""
from __future__ import annotations

import json
from typing import Any, Optional

import requests

from core.logger import get_logger
from core.models import JobOffer

# ntfy (API JSON) espera la prioridad como entero 1-5
PRIORITY_MAP: dict[str, int] = {
    "min": 1,
    "low": 2,
    "default": 3,
    "high": 4,
    "urgent": 5,
    "max": 5,
}


class Notifier:
    """Envía notificaciones a un topic de ntfy usando la API JSON (UTF-8 seguro)."""

    def __init__(
        self,
        topic: str,
        server: str = "https://ntfy.sh",
        token: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        self.topic = (topic or "").strip()
        self.server = (server or "https://ntfy.sh").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        self.log = get_logger("notifier")

    @property
    def enabled(self) -> bool:
        """True si hay topic configurado."""
        return bool(self.topic)

    # ------------------------------------------------------------------ core
    def _publish(self, payload: dict[str, Any]) -> bool:
        """Publica un mensaje en ntfy. Devuelve True si fue aceptado."""
        if not self.enabled:
            self.log.warning("NTFY_TOPIC no configurado: notificación omitida")
            return False
        payload["topic"] = self.topic
        priority = payload.get("priority", "default")
        if isinstance(priority, str):
            payload["priority"] = PRIORITY_MAP.get(priority.lower(), 3)
        if not payload.get("actions"):
            payload.pop("actions", None)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.post(
                self.server,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.log.error("Fallo al notificar por ntfy: %s", exc)
            return False
        return True

    # ------------------------------------------------------------- mensajes
    def send_text(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: Optional[list[str]] = None,
        click: Optional[str] = None,
    ) -> bool:
        """Envía una notificación simple de texto."""
        payload: dict[str, Any] = {
            "title": title,
            "message": message,
            "priority": priority,
            "tags": tags or ["briefcase"],
        }
        if click:
            payload["click"] = click
        ok = self._publish(payload)
        if ok:
            self.log.info("Notificación enviada: %s", title[:80])
        return ok

    def send_startup(self) -> bool:
        """Notificación de arranque del bot."""
        return self.send_text(
            "✅ Job Hunter Bot activo en tu VPS",
            "El bot está corriendo y buscará ofertas junior/trainee/practicante "
            "en cada ciclo. Recibirás una notificación por cada oferta nueva con buen match.",
            priority="default",
            tags=["white_check_mark", "briefcase"],
        )

    def send_test(self) -> bool:
        """Notificación de prueba (`--test-notify`)."""
        return self.send_text(
            "🔔 Prueba de notificación — Job Hunter Bot",
            "Si ves este mensaje en tu celular, ntfy está correctamente configurado.\n"
            f"Topic: {self.topic}\nServidor: {self.server}",
            priority="high",
            tags=["bell", "white_check_mark"],
        )

    def send_job(self, offer: JobOffer) -> bool:
        """Notifica una oferta con formato enriquecido."""
        remote_flag = " (Remoto 🌎)" if offer.is_remote else ""
        lines = [f"📍 {offer.location or 'Ubicación no especificada'}{remote_flag}"]
        if offer.salary:
            lines.append(f"💰 {offer.salary}")
        lines.append(f"⭐ Match: {offer.score}/100 | Fuente: {offer.source}")
        if offer.matched_skills:
            lines.append(f"🎯 Skills de mi CV encontradas: {', '.join(offer.matched_skills[:8])}")
        if offer.ai_reason:
            lines.append(f"🤖 IA: {offer.ai_reason}")
        summary = offer.summary(300)
        if summary:
            lines.extend(["", summary])
        lines.extend(["", f"🔗 {offer.url}"])

        payload: dict[str, Any] = {
            "title": f"💼 {offer.title} — {offer.company or 'Empresa no especificada'}",
            "message": "\n".join(lines),
            "priority": "high" if offer.score >= 80 else "default",
            "tags": ["briefcase", offer.source],
            "click": offer.url,
            "actions": [
                {"action": "view", "label": "Ver oferta", "url": offer.url, "clear": True}
            ]
            if offer.url
            else [],
        }
        ok = self._publish(payload)
        if ok:
            self.log.info(
                "Notificada oferta [%s] %s — %s (score %s)",
                offer.source,
                offer.title[:60],
                offer.company[:40],
                offer.score,
            )
        return ok

    def send_overflow(self, remaining: int) -> bool:
        """Resumen cuando hay más ofertas que el máximo por ciclo."""
        return self.send_text(
            "📋 Más ofertas encontradas",
            f"Se encontraron {remaining} ofertas más con buen match en este ciclo. "
            "Revisa el log (logs/bot.log) o ejecuta `python main.py --stats` para verlas.",
            priority="low",
            tags=["clipboard"],
        )
    def send_cycle_summary(
        self,
        sources_ok: int,
        failed_sources: dict[str, str],
        raw_offers: int,
        new_jobs: int,
        notified: int,
        nutricion_new: int = 0,
    ) -> bool:
        """Resumen de fin de ciclo: fuentes, ofertas crudas, nuevas y notificadas."""
        lines = [
            f"✅ Fuentes OK: {sources_ok} | ❌ Fallidas: {len(failed_sources)}",
            f"📦 Ofertas crudas: {raw_offers}",
            f"🆕 Nuevas guardadas: {new_jobs} | 🔔 Notificadas: {notified}",
        ]
        if nutricion_new:
            lines.append(f"🥗 Nutrición (Arequipa): {nutricion_new} nuevas")
        if failed_sources:
            detail = ", ".join(list(failed_sources)[:6])
            lines.append(f"⚠️ Fallaron: {detail}")
        return self.send_text(
            "📊 Job Hunter Bot — ciclo completado",
            "\n".join(lines),
            priority="min" if new_jobs == 0 else "low",
            tags=["bar_chart"],
        )

    def send_all_sources_failed(self, errors: dict[str, str]) -> bool:
        """Alerta de prioridad alta si todas las fuentes fallaron."""
        detail = "\n".join(f"• {src}: {err[:80]}" for src, err in list(errors.items())[:10])
        return self.send_text(
            "🚨 Job Hunter Bot: todas las fuentes fallaron",
            "Ningún portal respondió en este ciclo. Revisa la conectividad del VPS "
            f"o los logs.\n\n{detail}",
            priority="high",
            tags=["rotating_light", "warning"],
        )
