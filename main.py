#!/usr/bin/env python3
"""Job Hunter Bot - entrypoint.

Busca ofertas de empleo junior/trainee/practicante en múltiples portales
(Perú + internacional), las puntúa, deduplica y notifica vía ntfy.sh.

Uso:
    python main.py                      # scheduler 24/7 (ciclo inmediato + cada N min)
    python main.py --once               # un solo ciclo y salir
    python main.py --test-notify        # prueba de notificación ntfy
    python main.py --source computrabajo  # prueba una sola fuente
    python main.py --stats              # estadísticas de la base de datos
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.database import Database  # noqa: E402
from core.http_client import HttpClient  # noqa: E402
from core.logger import setup_logger  # noqa: E402
from core.matcher import Matcher  # noqa: E402
from core.matcher_ai import AIMatcher  # noqa: E402
from core.matcher_nutricion import NutricionMatcher  # noqa: E402
from core.models import JobOffer, clean_html, matches_any_keyword, normalize_text  # noqa: E402
from core.notifier import Notifier  # noqa: E402
from scrapers.adzuna_api import AdzunaScraper  # noqa: E402
from scrapers.arbeitnow_api import ArbeitnowScraper  # noqa: E402
from scrapers.base import BaseScraper, ScraperError, SkipSource  # noqa: E402
from scrapers.bebee import BebeeScraper  # noqa: E402
from scrapers.bumeran import BumeranScraper  # noqa: E402
from scrapers.buscojobs import BuscojobsScraper  # noqa: E402
from scrapers.computrabajo import ComputrabajoScraper  # noqa: E402
from scrapers.expertini import ExpertiniScraper  # noqa: E402
from scrapers.getonboard_api import GetOnBoardScraper  # noqa: E402
from scrapers.google_careers import GoogleCareersScraper  # noqa: E402
from scrapers.jobrapido import JobrapidoScraper  # noqa: E402
from scrapers.jooble_api import JoobleScraper  # noqa: E402
from scrapers.jsearch_api import JSearchScraper  # noqa: E402
from scrapers.kitempleo import KitempleoScraper  # noqa: E402
from scrapers.linkedin_guest import LinkedInBlocked, LinkedInGuestScraper  # noqa: E402
from scrapers.meta_careers import MetaCareersScraper  # noqa: E402
from scrapers.microsoft_careers import MicrosoftCareersScraper  # noqa: E402
from scrapers.pandape import PandapeScraper  # noqa: E402
from scrapers.remoteok_api import RemoteOkScraper  # noqa: E402
from scrapers.remotive_api import RemotiveScraper  # noqa: E402
from scrapers.teamtailor import TeamtailorScraper  # noqa: E402
from scrapers.themuse_api import TheMuseScraper  # noqa: E402

SCRAPER_CLASSES: tuple[type[BaseScraper], ...] = (
    # NIVEL A
    JoobleScraper,
    AdzunaScraper,
    RemotiveScraper,
    RemoteOkScraper,
    ArbeitnowScraper,
    TheMuseScraper,
    GetOnBoardScraper,
    GoogleCareersScraper,
    MicrosoftCareersScraper,
    MetaCareersScraper,
    TeamtailorScraper,
    JSearchScraper,
    # NIVEL B
    ComputrabajoScraper,
    PandapeScraper,
    KitempleoScraper,
    BuscojobsScraper,
    BumeranScraper,
    ExpertiniScraper,
    JobrapidoScraper,
    BebeeScraper,
    # NIVEL C
    LinkedInGuestScraper,
)

DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.yaml")


# --------------------------------------------------------------------- config
def load_config(path: str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Carga y valida mínimamente el config.yaml."""
    if not os.path.exists(path):
        raise SystemExit(f"No se encontró el archivo de configuración: {path}")
    with open(path, "r", encoding="utf-8") as handler:
        config = yaml.safe_load(handler) or {}
    if not isinstance(config, dict):
        raise SystemExit("config.yaml inválido: se esperaba un mapa YAML")
    keywords = ((config.get("search") or {}).get("keywords")) or []
    if not keywords:
        raise SystemExit("config.yaml: define al menos una keyword en search.keywords")
    return config


def build_components(config: dict[str, Any]) -> tuple[Database, Notifier, Matcher, HttpClient]:
    """Instancia base de datos, notificador, matcher y cliente HTTP."""
    advanced = config.get("advanced", {}) or {}
    db_path = str(advanced.get("database_path", "jobs.db"))
    if not os.path.isabs(db_path):
        db_path = os.path.join(BASE_DIR, db_path)
    database = Database(db_path)
    notifier = Notifier(
        topic=os.environ.get("NTFY_TOPIC", ""),
        server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
        token=os.environ.get("NTFY_TOKEN", ""),
        timeout=int(advanced.get("request_timeout", 20)),
    )
    matcher = Matcher(config)
    http = HttpClient(
        timeout=int(advanced.get("request_timeout", 20)),
        min_delay=float(advanced.get("min_delay_seconds", 2)),
        max_delay=float(advanced.get("max_delay_seconds", 5)),
        max_retries=int(advanced.get("max_retries", 3)),
    )
    return database, notifier, matcher, http


def build_scrapers(
    config: dict[str, Any], http: HttpClient, only: Optional[str] = None
) -> list[BaseScraper]:
    """Crea las instancias de los scrapers habilitados en config.sources."""
    enabled = config.get("sources", {}) or {}
    scrapers: list[BaseScraper] = []
    for scraper_class in SCRAPER_CLASSES:
        name = scraper_class.name
        if only:
            if name != only:
                continue
        elif not enabled.get(name, False):
            continue
        scrapers.append(scraper_class(http, config))
    return scrapers


# ---------------------------------------------------------------- enriquecer
def enrich_offers(
    offers: list[JobOffer],
    http: HttpClient,
    matcher: Matcher,
    matching: dict[str, Any],
    extra_title_terms: tuple[str, ...] = (),
) -> int:
    """Descarga la descripción completa de las ofertas candidatas con texto corto.

    Solo se enriquecen las ofertas cuyo título ya parece de perfil junior y que no
    tienen palabras excluyentes, para no gastar requests innecesarios.

    Returns:
        Número de ofertas enriquecidas.
    """
    min_chars = int(matching.get("enrich_min_description_chars", 400))
    budget = int(matching.get("max_detail_fetches_per_cycle", 30))
    if budget <= 0:
        return 0

    candidates: list[JobOffer] = []
    for offer in offers:
        if not offer.url or len(offer.description or "") >= min_chars:
            continue
        title = normalize_text(offer.title)
        if any(word and word in title for word in matcher.exclude_keywords):
            continue
        looks_junior = any(word and word in title for word in matcher.seniority)
        looks_extra = any(term and term in title for term in extra_title_terms)
        if not looks_junior and not looks_extra and not matches_any_keyword(title, matcher.keywords):
            continue
        candidates.append(offer)

    enriched = 0
    for offer in candidates[:budget]:
        description = http.fetch_description(offer.url)
        if description and len(description) > len(offer.description or ""):
            offer.description = clean_html(description)
            offer.detail_fetched = True
            enriched += 1
    return enriched


# ---------------------------------------------------------------------- ciclo
def run_cycle(
    config: dict[str, Any],
    database: Database,
    notifier: Notifier,
    matcher: Matcher,
    http: HttpClient,
    only_source: Optional[str] = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Ejecuta un ciclo completo de búsqueda, scoring, dedup y notificación."""
    log = setup_logger()
    advanced = config.get("advanced", {}) or {}
    matching = config.get("matching", {}) or {}
    search = config.get("search", {}) or {}
    locations = search.get("locations", {}) or {}
    keywords: list[str] = [k for k in (search.get("keywords") or []) if k]
    
    # --- NUEVO: perfil nutricionista + notificador de su topic separado ---
    nutricion = NutricionMatcher(config)
    if nutricion.enabled:
        keywords = keywords + [k for k in nutricion.keywords if k not in keywords]
    notifier_nutricion = Notifier(
        topic=os.environ.get("NTFY_TOPIC_NUTRICION", ""),
        server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
        token=os.environ.get("NTFY_TOKEN", ""),
        timeout=int(advanced.get("request_timeout", 20)),
    )
    
    started_at = datetime.now(timezone.utc).isoformat()
    scrapers = build_scrapers(config, http, only=only_source)
    if not scrapers:
        log.warning("No hay fuentes activas (revisa `sources` en config.yaml)")
        return {"sources_ok": 0, "sources_failed": 0, "new_jobs": 0, "notified": 0, "offers": []}

    log.info(
        "Iniciando ciclo | %s fuentes | %s keywords | umbral %s",
        len(scrapers),
        len(keywords),
        matcher.min_score,
    )

    all_offers: list[JobOffer] = []
    ok_sources = 0
    failed_sources: dict[str, str] = {}
    skipped_sources: dict[str, str] = {}

    for scraper in scrapers:
        disabled, until = database.is_source_disabled(scraper.name)
        if disabled and not only_source:
            log.info("[%s] desactivada por circuit breaker hasta %s", scraper.name, until)
            skipped_sources[scraper.name] = f"circuit breaker hasta {until}"
            continue
        try:
            offers = scraper.fetch_jobs(keywords, locations)
        except SkipSource as exc:
            log.warning("[%s] omitida: %s", scraper.name, exc)
            skipped_sources[scraper.name] = str(exc)
            continue
        except LinkedInBlocked as exc:
            hours = int(advanced.get("circuit_breaker_hours", 6))
            database.disable_source(scraper.name, hours, str(exc))
            log.error("[%s] bloqueada (%s): desactivada %sh", scraper.name, exc, hours)
            failed_sources[scraper.name] = str(exc)
            continue
        except (ScraperError, Exception) as exc:  # noqa: BLE001 - nunca crashear
            disabled_now = database.record_failure(
                scraper.name,
                str(exc),
                max_failures=int(advanced.get("circuit_breaker_failures", 3)),
                disable_hours=int(advanced.get("circuit_breaker_hours", 6)),
            )
            log.error(
                "[%s] falló: %s%s",
                scraper.name,
                str(exc)[:220],
                " -> desactivada temporalmente" if disabled_now else "",
            )
            failed_sources[scraper.name] = str(exc)
            continue

        ok_sources += 1
        database.record_success(scraper.name, len(offers))
        log.info("[%s] %s ofertas crudas", scraper.name, len(offers))
        all_offers.extend(offers)

    # ------------------------------------------------------------ enriquecer
    if matching.get("enrich_details", True):
        enriched = enrich_offers(all_offers, http, matcher, matching, tuple(nutricion.must_have_title))
        if enriched:
            log.info("Descripciones completas descargadas: %s ofertas", enriched)

    # ---------------------------------------------------------------- scoring
    accepted = matcher.evaluate(all_offers)

    # --------------------------------------------- ETAPA 3 (opcional): IA
    ai_matcher = AIMatcher(config, database)
    if ai_matcher.available and accepted:
        rescored: list[JobOffer] = []
        for offer in accepted:
            final_score, _reason = ai_matcher.evaluate(offer, offer.score)
            offer.score = final_score
            if final_score >= matcher.min_score:
                rescored.append(offer)
        rescored.sort(key=lambda o: o.score, reverse=True)
        log.info(
            "Matching con IA (%s): %s consultas, %s ofertas sobreviven el umbral",
            ai_matcher.model,
            ai_matcher.calls_made,
            len(rescored),
        )
        accepted = rescored
        
    # --------- NUEVO: perfil NUTRICIONISTA (solo Arequipa, sin SERUMS) ---------
    if nutricion.enabled:
        nutri_accepted = nutricion.evaluate(all_offers)
        accepted_ids = {o.job_id for o in accepted}
        added = 0
        for offer in nutri_accepted:
            if offer.job_id in accepted_ids:
                continue
            offer.raw["perfil"] = "nutricion"  # marca para enrutar al topic de nutrición
            accepted.append(offer)
            added += 1
        if added:
            accepted.sort(key=lambda o: o.score, reverse=True)
            log.info("Perfil nutricion: %s ofertas aceptadas", added)
    
    log.info(
        "Ofertas crudas: %s | con score >= %s: %s",
        len(all_offers),
        matcher.min_score,
        len(accepted),
    )

    dedup_days = int(matching.get("cross_source_dedup_days", 7))
    new_offers: list[JobOffer] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for offer in accepted:
        if offer.job_id in seen_ids or offer.dedup_key in seen_keys:
            continue
        if database.exists(offer.job_id):
            continue
        if database.notified_recently(offer.dedup_key, dedup_days):
            continue
        seen_ids.add(offer.job_id)
        seen_keys.add(offer.dedup_key)
        if database.save_job(offer):
            new_offers.append(offer)

    # ----------------------------------------------------------- notificación
    max_notifications = int(matching.get("max_notifications_per_cycle", 15))
    notified = 0
    if notify and new_offers:
        for offer in new_offers[:max_notifications]:
            # NUEVO: las ofertas de nutrición van a su propio topic (si está configurado)
            is_nutricion = offer.raw.get("perfil") == "nutricion"
            target = notifier_nutricion if (is_nutricion and notifier_nutricion.enabled) else notifier
            if target.send_job(offer):
                database.mark_notified(offer.job_id)
                notified += 1
        remaining = len(new_offers) - max_notifications
        if remaining > 0:
            notifier.send_overflow(remaining)

    if notify and scrapers and ok_sources == 0 and failed_sources:
        notifier.send_all_sources_failed(failed_sources)

    database.cleanup(int(matching.get("retention_days", 60)))
    database.record_cycle(started_at, ok_sources, len(failed_sources), len(new_offers), notified)

    log.info(
        "Ciclo completado: %s fuentes OK, %s fallidas, %s ofertas nuevas, %s notificadas",
        ok_sources,
        len(failed_sources),
        len(new_offers),
        notified,
    )
    if skipped_sources:
        log.info("Fuentes omitidas: %s", ", ".join(skipped_sources))

    return {
        "sources_ok": ok_sources,
        "sources_failed": len(failed_sources),
        "failed_detail": failed_sources,
        "skipped": skipped_sources,
        "raw_offers": len(all_offers),
        "accepted": len(accepted),
        "new_jobs": len(new_offers),
        "notified": notified,
        "offers": new_offers,
    }


# ------------------------------------------------------------------------ CLI
def print_stats(database: Database) -> None:
    """Imprime las estadísticas de la base de datos."""
    stats = database.stats()
    print("\n=== Job Hunter Bot | estadísticas ===")
    print(f"Ofertas almacenadas : {stats['total_jobs']}")
    print(f"Ofertas notificadas : {stats['notified_jobs']}")
    print("\n-- Por fuente --")
    if not stats["by_source"]:
        print("  (aún sin datos)")
    for row in stats["by_source"]:
        print(
            f"  {row['source']:<20} ofertas={row['jobs']:<5} notificadas={row['notified'] or 0:<5} "
            f"score medio={row['avg_score']}"
        )
    print("\n-- Estado de fuentes --")
    for row in stats["source_state"]:
        state = "OK"
        if row.get("disabled_until"):
            state = f"desactivada hasta {row['disabled_until']}"
        elif row.get("consecutive_failures"):
            state = f"{row['consecutive_failures']} fallos seguidos"
        print(f"  {row['source']:<20} {state}")
        if row.get("last_error"):
            print(f"      último error: {str(row['last_error'])[:110]}")
    print("\n-- Últimos ciclos --")
    for row in stats["last_cycles"]:
        print(
            f"  {row['started_at'][:19]} | OK={row['sources_ok']} fallidas={row['sources_failed']} "
            f"nuevas={row['new_jobs']} notificadas={row['notified']}"
        )
    print("\n-- Últimas ofertas notificadas --")
    for row in stats["last_notified"]:
        skills = f" | skills: {row['matched_skills']}" if row.get("matched_skills") else ""
        print(
            f"  [{row['score']:>3}] {row['title'][:52]} — {row['company'][:24]} "
            f"({row['source']}){skills}"
        )
    print()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Define los argumentos del CLI."""
    parser = argparse.ArgumentParser(description="Job Hunter Bot")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="ruta a config.yaml")
    parser.add_argument("--once", action="store_true", help="ejecuta un solo ciclo y termina")
    parser.add_argument(
        "--test-notify", action="store_true", help="envía una notificación de prueba a ntfy"
    )
    parser.add_argument("--source", help="ejecuta solo una fuente (ej: computrabajo)")
    parser.add_argument("--stats", action="store_true", help="muestra estadísticas de la BD")
    parser.add_argument(
        "--no-notify", action="store_true", help="no envía notificaciones (modo prueba)"
    )
    parser.add_argument(
        "--list-sources", action="store_true", help="lista las fuentes disponibles"
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Punto de entrada del bot."""
    args = parse_args(argv)
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    config = load_config(args.config)
    advanced = config.get("advanced", {}) or {}
    log_file = str(advanced.get("log_file", "logs/bot.log"))
    if not os.path.isabs(log_file):
        log_file = os.path.join(BASE_DIR, log_file)
    log = setup_logger(log_file, str(advanced.get("log_level", "INFO")))

    if args.list_sources:
        print("\nFuentes disponibles (nivel | nombre | credenciales):")
        for scraper_class in SCRAPER_CLASSES:
            creds = ", ".join(scraper_class.requires_env) or "-"
            enabled = (config.get("sources") or {}).get(scraper_class.name, False)
            print(
                f"  [{scraper_class.tier}] {scraper_class.name:<20} {creds:<34} "
                f"{'ON' if enabled else 'off'}"
            )
        print()
        return 0

    database, notifier, matcher, http = build_components(config)

    try:
        if args.stats:
            print_stats(database)
            return 0

        if args.test_notify:
            ok = notifier.send_test()
            print(
                "Notificación de prueba enviada correctamente"
                if ok
                else "No se pudo enviar la notificación (revisa NTFY_TOPIC/NTFY_SERVER)"
            )
            # NUEVO: prueba también el topic de nutrición si está configurado
            topic_nutri = os.environ.get("NTFY_TOPIC_NUTRICION", "").strip()
            if topic_nutri:
                notifier_nutri = Notifier(
                    topic=topic_nutri,
                    server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
                    token=os.environ.get("NTFY_TOKEN", ""),
                )
                ok_nutri = notifier_nutri.send_test()
                print(
                    f"Notificación de prueba enviada al topic de NUTRICIÓN ({topic_nutri})"
                    if ok_nutri
                    else "No se pudo enviar al topic de nutrición (revisa NTFY_TOPIC_NUTRICION)"
                )
            else:
                print("NTFY_TOPIC_NUTRICION no configurado: no se probó el topic de nutrición")
            return 0 if ok else 1

        if args.source:
            valid = {cls.name for cls in SCRAPER_CLASSES}
            if args.source not in valid:
                print(f"Fuente desconocida: {args.source}\nDisponibles: {', '.join(sorted(valid))}")
                return 2
            result = run_cycle(
                config,
                database,
                notifier,
                matcher,
                http,
                only_source=args.source,
                notify=not args.no_notify,
            )
            print(
                f"\nFuente {args.source}: crudas={result['raw_offers']} "
                f"aceptadas={result['accepted']} nuevas={result['new_jobs']} "
                f"notificadas={result['notified']}"
            )
            if result["failed_detail"]:
                for source, error in result["failed_detail"].items():
                    print(f"  ERROR {source}: {error[:200]}")
            return 0

        if args.once:
            run_cycle(config, database, notifier, matcher, http, notify=not args.no_notify)
            return 0

        # ------------------------------------------------------ modo 24/7
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        import pytz

        scheduler_config = config.get("scheduler", {}) or {}
        interval = int(scheduler_config.get("interval_minutes", 60))
        timezone_name = str(scheduler_config.get("timezone", "America/Lima"))
        tzinfo = pytz.timezone(timezone_name)

        if not args.no_notify:
            notifier.send_startup()

        def job() -> None:
            """Wrapper del ciclo para el scheduler (nunca propaga excepciones)."""
            try:
                run_cycle(config, database, notifier, matcher, http, notify=not args.no_notify)
            except Exception as exc:  # noqa: BLE001
                log.exception("Error inesperado en el ciclo: %s", exc)

        scheduler = BlockingScheduler(timezone=tzinfo)
        scheduler.add_job(
            job,
            IntervalTrigger(minutes=interval, timezone=tzinfo),
            id="job_hunter_cycle",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
        log.info(
            "Scheduler iniciado: cada %s minutos (%s). Primera ejecución inmediata.",
            interval,
            timezone_name,
        )
        job()  # primera corrida inmediata
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Bot detenido por el usuario")
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
