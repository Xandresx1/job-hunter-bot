"""Tests del matcher basado en el CV (`cv_profile` de config.yaml).

Ejecutar:
    python -m tests.test_matcher      # desde la carpeta del bot
    pytest tests/test_matcher.py -s

Casos exigidos en los criterios de aceptación:
  * "Senior PHP Developer"                       -> descartada
  * oferta que pide 5 años de experiencia        -> descartada
  * oferta que exige inglés avanzado             -> descartada
  * "Practicante de desarrollo React - Arequipa" -> aceptada
  * "Junior Backend Developer (Node.js) - Remote LATAM" -> aceptada
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import setup_logger  # noqa: E402
from core.matcher import Matcher  # noqa: E402
from core.matcher_ai import AIMatcher  # noqa: E402
from core.models import JobOffer  # noqa: E402
from main import load_config  # noqa: E402

setup_logger(os.path.join(tempfile.gettempdir(), "job_hunter_matcher_test.log"), "ERROR")
CONFIG = load_config(os.path.join(BASE_DIR, "config.yaml"))
MATCHER = Matcher(CONFIG)


def offer(
    title: str,
    description: str = "",
    location: str = "Arequipa, Perú",
    hours_old: int = 5,
    company: str = "ACME SAC",
) -> JobOffer:
    """Crea una oferta de prueba."""
    return JobOffer(
        title=title,
        company=company,
        location=location,
        url=f"https://example.com/jobs/{abs(hash(title)) % 10**8}",
        description=description,
        source="test",
        posted_at=datetime.now(timezone.utc) - timedelta(hours=hours_old),
    )


# ------------------------------------------------------------- filtros duros
def test_reject_senior_php() -> None:
    """'Senior PHP Developer' debe descartarse (seniority + stack incompatible)."""
    result = MATCHER.evaluate_offer(
        offer("Senior PHP Developer", "Buscamos senior con Laravel y PHP para nuestro equipo.")
    )
    assert result.score == 0, f"debería ser 0, fue {result.score}"
    assert result.reject_reason, "debe indicar el motivo del descarte"
    print(f"OK descarta senior: {result.reject_reason}")


def test_reject_only_php_stack() -> None:
    """Un junior de stack 100% incompatible (PHP/Laravel) se descarta."""
    result = MATCHER.evaluate_offer(
        offer(
            "Programador Junior Laravel",
            "Desarrollo con PHP 8, Laravel y Symfony. Se valora experiencia con Drupal.",
        )
    )
    assert result.score == 0, f"debería ser 0, fue {result.score}"
    print(f"OK descarta stack incompatible: {result.reject_reason}")


def test_reject_five_years_experience() -> None:
    """Una oferta que pide 5 años de experiencia se descarta."""
    result = MATCHER.evaluate_offer(
        offer(
            "Desarrollador Junior Python",
            "Requisitos: 5 años de experiencia en desarrollo Python y Django. Trabajo en Arequipa.",
        )
    )
    assert result.score == 0, f"debería ser 0, fue {result.score}"
    assert "anos" in result.reject_reason
    print(f"OK descarta por experiencia: {result.reject_reason}")


def test_accept_two_years_experience() -> None:
    """Hasta max_years_experience (2) sí se acepta."""
    result = MATCHER.evaluate_offer(
        offer(
            "Practicante de desarrollo de software",
            "Deseable 1 año de experiencia con JavaScript, React y SQL. Modalidad híbrida en Arequipa.",
        )
    )
    assert result.score >= MATCHER.min_score, f"debería pasar el umbral, fue {result.score}"
    print(f"OK acepta 1 año de experiencia: score={result.score}")


def test_reject_advanced_english() -> None:
    """Una oferta que exige inglés avanzado se descarta."""
    result = MATCHER.evaluate_offer(
        offer(
            "Junior Software Developer",
            "We need JavaScript and React skills. Advanced English (C1) is required for daily calls.",
            location="Remote LATAM",
        )
    )
    assert result.score == 0, f"debería ser 0, fue {result.score}"
    assert "ingles" in result.reject_reason
    print(f"OK descarta inglés avanzado: {result.reject_reason}")


def test_accept_basic_english() -> None:
    """Inglés básico/técnico NO descarta la oferta."""
    result = MATCHER.evaluate_offer(
        offer(
            "Junior Backend Developer",
            "Node.js, SQL y Git. Inglés básico para lectura técnica de documentación.",
            location="Remoto - Perú",
        )
    )
    assert result.score >= MATCHER.min_score, f"debería pasar el umbral, fue {result.score}"
    print(f"OK acepta inglés básico: score={result.score}")


def test_reject_non_dev_role() -> None:
    """Un practicante de otro rubro (marketing/ventas) se descarta."""
    for title, description in (
        ("Practicante Área Marketing", "Apoyo en campañas, redes sociales y community manager."),
        ("Asesor de ventas junior", "Venta de productos en tienda, atención al cliente."),
        ("Practicante de Call Center", "Atención telefónica a clientes, cobranzas."),
    ):
        result = MATCHER.evaluate_offer(offer(title, description))
        assert result.score == 0, f"'{title}' debería ser 0, fue {result.score}"
    print("OK descarta rubros no relacionados a desarrollo")


# ------------------------------------------------------------------ aceptadas
def test_accept_practicante_react_arequipa() -> None:
    """'Practicante de desarrollo React - Arequipa' debe aceptarse."""
    result = MATCHER.evaluate_offer(
        offer(
            "Practicante de desarrollo React - Arequipa",
            "Buscamos estudiante de Ingeniería de Sistemas para apoyar en desarrollo frontend con "
            "React, JavaScript, HTML y CSS. Modalidad presencial en Arequipa con mentoría.",
        )
    )
    assert result.score >= MATCHER.min_score, f"debería pasar el umbral, fue {result.score}"
    assert "react" in result.matched_skills
    print(f"OK acepta practicante React Arequipa: score={result.score} skills={result.matched_skills}")


def test_accept_junior_node_remote_latam() -> None:
    """'Junior Backend Developer (Node.js) - Remote LATAM' debe aceptarse."""
    result = MATCHER.evaluate_offer(
        offer(
            "Junior Backend Developer (Node.js)",
            "Remote worldwide role for LATAM candidates. Stack: Node.js, JavaScript, SQL, Git. "
            "No previous professional experience required, we provide training.",
            location="Remote LATAM",
        )
    )
    assert result.score >= MATCHER.min_score, f"debería pasar el umbral, fue {result.score}"
    assert "node.js" in result.matched_skills or "nodejs" in result.matched_skills
    print(f"OK acepta junior Node remoto LATAM: score={result.score} skills={result.matched_skills}")


def test_score_breakdown_and_skills() -> None:
    """El desglose del score y las skills matcheadas se exponen correctamente."""
    result = MATCHER.evaluate_offer(
        offer(
            "Trainee Full Stack Developer",
            "Stack: JavaScript, React, Node.js, SQL, TypeScript, Git. Programa de formación para "
            "primer empleo, sin experiencia previa. Híbrido en Arequipa.",
        )
    )
    assert result.score >= 80, f"perfil ideal debería superar 80, fue {result.score}"
    assert "titulo_junior" in result.breakdown and "skills_cv" in result.breakdown
    print(f"OK desglose: {result.breakdown} skills={result.matched_skills}")


def test_ai_matcher_disabled_by_default() -> None:
    """Con use_ai_matching: false la IA no se usa y no rompe nada."""
    ai = AIMatcher(CONFIG, None)
    score, reason = ai.evaluate(offer("Junior Developer", "React y Node.js"), 70)
    assert ai.available is False
    assert score == 70 and reason == ""
    print("OK matcher de IA desactivado por defecto (degradación limpia)")


def main() -> int:
    """Ejecuta todos los tests del matcher."""
    tests = [
        test_reject_senior_php,
        test_reject_only_php_stack,
        test_reject_five_years_experience,
        test_accept_two_years_experience,
        test_reject_advanced_english,
        test_accept_basic_english,
        test_reject_non_dev_role,
        test_accept_practicante_react_arequipa,
        test_accept_junior_node_remote_latam,
        test_score_breakdown_and_skills,
        test_ai_matcher_disabled_by_default,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FALLÓ {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print("\nResultado matcher:", "TODO OK" if failures == 0 else f"{failures} fallo(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
