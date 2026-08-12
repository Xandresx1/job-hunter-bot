"""Smoke test: 1 keyword contra 3 fuentes API + notificación de prueba.

Ejecutar:
    python -m tests.test_smoke        # desde la carpeta del bot
    pytest tests/test_smoke.py -s     # si prefieres pytest

No requiere API keys: usa Remotive, RemoteOK y Arbeitnow (sin credenciales).
La notificación solo se envía si NTFY_TOPIC está definido en .env.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv  # noqa: E402

from core.database import Database  # noqa: E402
from core.http_client import HttpClient  # noqa: E402
from core.logger import setup_logger  # noqa: E402
from core.matcher import Matcher  # noqa: E402
from core.models import JobOffer  # noqa: E402
from core.notifier import Notifier  # noqa: E402
from main import load_config  # noqa: E402
from scrapers.arbeitnow_api import ArbeitnowScraper  # noqa: E402
from scrapers.remoteok_api import RemoteOkScraper  # noqa: E402
from scrapers.remotive_api import RemotiveScraper  # noqa: E402

load_dotenv(os.path.join(BASE_DIR, ".env"))
setup_logger(os.path.join(tempfile.gettempdir(), "job_hunter_test.log"), "WARNING")
CONFIG = load_config(os.path.join(BASE_DIR, "config.yaml"))


def _sample_offer(title: str = "Junior Python Developer", **kwargs) -> JobOffer:
    """Crea una oferta de prueba."""
    data = {
        "title": title,
        "company": "ACME SAC",
        "location": "Arequipa, Perú",
        "url": "https://example.com/jobs/123?utm=x",
        "description": "Buscamos practicante con conocimientos de python y react.",
        "source": "test",
        "posted_at": datetime.now(timezone.utc) - timedelta(hours=3),
    }
    data.update(kwargs)
    return JobOffer(**data)


def test_scoring_and_exclusions() -> None:
    """El matcher acepta juniors y descarta seniors."""
    matcher = Matcher(CONFIG)
    junior = _sample_offer()
    senior = _sample_offer(title="Senior Python Developer")
    assert matcher.score(junior) >= matcher.min_score, "la oferta junior debería pasar el umbral"
    assert matcher.score(senior) == 0, "la oferta senior debe descartarse"
    print(f"OK scoring: junior={matcher.score(junior)} senior={matcher.score(senior)}")


def test_database_dedup() -> None:
    """La misma oferta no se guarda dos veces."""
    path = os.path.join(tempfile.mkdtemp(), "test_jobs.db")
    database = Database(path)
    offer = _sample_offer()
    assert database.save_job(offer) is True
    assert database.save_job(offer) is False, "la segunda inserción debe ignorarse"
    assert database.exists(offer.job_id) is True
    database.mark_notified(offer.job_id)
    assert database.notified_recently(offer.dedup_key, 7) is True
    # Misma oferta con sufijo de ciudad en el título -> misma huella de dedup
    variant = _sample_offer(
        title="Junior Python Developer - Arequipa",
        url="https://otro-portal.com/jobs/999",
    )
    assert variant.dedup_key == offer.dedup_key, "la huella de dedup debe ser tolerante"
    assert database.notified_recently(variant.dedup_key, 7) is True

    stats = database.stats()
    assert stats["total_jobs"] == 1 and stats["notified_jobs"] == 1
    database.close()
    print("OK deduplicación y persistencia SQLite")


def test_api_sources() -> None:
    """Consulta 3 fuentes API sin key con una keyword del config."""
    keyword = (CONFIG["search"]["keywords"] or ["junior developer"])[0]
    locations = CONFIG["search"]["locations"]
    http = HttpClient(timeout=20, min_delay=1, max_delay=2, max_retries=2)
    results: dict[str, int] = {}
    for scraper_class in (RemotiveScraper, RemoteOkScraper, ArbeitnowScraper):
        scraper = scraper_class(http, CONFIG)
        try:
            offers = scraper.fetch_jobs([keyword], locations)
            results[scraper.name] = len(offers)
        except Exception as exc:  # noqa: BLE001
            results[scraper.name] = -1
            print(f"AVISO {scraper.name} falló: {exc}")
    print(f"OK fuentes API: {results}")
    assert any(count > 0 for count in results.values()), "ninguna fuente API respondió"


def test_notification() -> None:
    """Envía la notificación de prueba si hay NTFY_TOPIC configurado."""
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        print("AVISO: NTFY_TOPIC no configurado; se omite la prueba de notificación")
        return
    notifier = Notifier(topic, os.environ.get("NTFY_SERVER", "https://ntfy.sh"))
    assert notifier.send_test() is True, "ntfy rechazó la notificación"
    print(f"OK notificación enviada al topic {topic}")


def main() -> int:
    """Ejecuta todos los smoke tests en orden."""
    tests = [
        test_scoring_and_exclusions,
        test_database_dedup,
        test_api_sources,
        test_notification,
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
    print("\nResultado:", "TODO OK" if failures == 0 else f"{failures} prueba(s) fallida(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
