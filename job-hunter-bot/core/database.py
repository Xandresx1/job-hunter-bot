"""Persistencia SQLite: ofertas, deduplicación y estado de fuentes."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.logger import get_logger
from core.models import JobOffer, dedup_fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    dedup_key   TEXT,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    salary      TEXT,
    url         TEXT,
    description TEXT,
    source      TEXT,
    score       INTEGER DEFAULT 0,
    is_remote   INTEGER DEFAULT 0,
    country     TEXT,
    posted_at   TEXT,
    matched_skills TEXT,
    ai_score    INTEGER,
    ai_reason   TEXT,
    created_at  TEXT NOT NULL,
    notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs (dedup_key);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs (source);

CREATE TABLE IF NOT EXISTS source_state (
    source              TEXT PRIMARY KEY,
    consecutive_failures INTEGER DEFAULT 0,
    disabled_until      TEXT,
    last_error          TEXT,
    last_success_at     TEXT,
    total_jobs          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_cache (
    job_id     TEXT PRIMARY KEY,
    ai_score   INTEGER,
    reason     TEXT,
    model      TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS cycles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    sources_ok  INTEGER,
    sources_failed INTEGER,
    new_jobs    INTEGER,
    notified    INTEGER
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Wrapper delgado y thread-safe sobre SQLite."""

    def __init__(self, path: str = "jobs.db") -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.log = get_logger("db")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()
        refreshed = self.refresh_dedup_keys()
        if refreshed:
            self.log.info("Huellas de deduplicación recalculadas: %s registros", refreshed)

    def _migrate(self) -> None:
        """Añade columnas nuevas a bases de datos creadas por versiones anteriores."""
        existing = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for column, ddl in (
            ("matched_skills", "ALTER TABLE jobs ADD COLUMN matched_skills TEXT"),
            ("ai_score", "ALTER TABLE jobs ADD COLUMN ai_score INTEGER"),
            ("ai_reason", "ALTER TABLE jobs ADD COLUMN ai_reason TEXT"),
        ):
            if column not in existing:
                self.conn.execute(ddl)

    def refresh_dedup_keys(self) -> int:
        """Recalcula las huellas de dedup de los registros existentes.

        Garantiza que una actualización del bot (que cambie la forma de calcular la
        huella) no vuelva a notificar ofertas que ya se enviaron.

        Returns:
            Número de registros actualizados.
        """
        with self._lock:
            rows = self.conn.execute("SELECT id, title, company, dedup_key FROM jobs").fetchall()
            updates: list[tuple[str, str]] = []
            for row in rows:
                fingerprint = dedup_fingerprint(row["title"] or "", row["company"] or "")
                key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                if key != (row["dedup_key"] or ""):
                    updates.append((key, row["id"]))
            if updates:
                self.conn.executemany(
                    "UPDATE jobs SET dedup_key = ? WHERE id = ?", updates
                )
                self.conn.commit()
        return len(updates)

    # ------------------------------------------------------------ dedup/jobs
    def exists(self, job_id: str) -> bool:
        """True si el hash de la oferta ya está en la base de datos."""
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row is not None

    def notified_recently(self, dedup_key: str, days: int = 7) -> bool:
        """True si una oferta equivalente (título+empresa) ya se notificó en N días."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM jobs WHERE dedup_key = ? AND notified_at IS NOT NULL "
                "AND notified_at >= ? LIMIT 1",
                (dedup_key, since),
            ).fetchone()
        return row is not None

    def save_job(self, offer: JobOffer) -> bool:
        """Inserta la oferta si es nueva. Devuelve True si se insertó."""
        row = offer.to_row()
        row["created_at"] = _utcnow()
        with self._lock:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (id, dedup_key, title, company, location, salary, url, description,
                    source, score, is_remote, country, posted_at, matched_skills,
                    ai_score, ai_reason, created_at)
                   VALUES (:id, :dedup_key, :title, :company, :location, :salary, :url,
                           :description, :source, :score, :is_remote, :country, :posted_at,
                           :matched_skills, :ai_score, :ai_reason, :created_at)""",
                row,
            )
            self.conn.commit()
        return cursor.rowcount > 0

    def mark_notified(self, job_id: str) -> None:
        """Marca la oferta como notificada."""
        with self._lock:
            self.conn.execute(
                "UPDATE jobs SET notified_at = ? WHERE id = ?", (_utcnow(), job_id)
            )
            self.conn.commit()

    def cleanup(self, retention_days: int = 60) -> int:
        """Borra registros con más de `retention_days` días. Devuelve filas borradas."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._lock:
            cursor = self.conn.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            self.conn.commit()
        if cursor.rowcount:
            self.log.info("Limpieza: %s ofertas antiguas eliminadas", cursor.rowcount)
        return cursor.rowcount

    # -------------------------------------------------------- circuit breaker
    def is_source_disabled(self, source: str) -> tuple[bool, Optional[str]]:
        """Indica si una fuente está temporalmente desactivada y hasta cuándo."""
        with self._lock:
            row = self.conn.execute(
                "SELECT disabled_until FROM source_state WHERE source = ?", (source,)
            ).fetchone()
        if not row or not row["disabled_until"]:
            return False, None
        try:
            until = datetime.fromisoformat(row["disabled_until"])
        except ValueError:
            return False, None
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > datetime.now(timezone.utc):
            return True, row["disabled_until"]
        return False, None

    def record_success(self, source: str, jobs_found: int) -> None:
        """Resetea el contador de fallos de una fuente."""
        with self._lock:
            self.conn.execute(
                """INSERT INTO source_state (source, consecutive_failures, disabled_until,
                                             last_error, last_success_at, total_jobs)
                   VALUES (?, 0, NULL, NULL, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       consecutive_failures = 0,
                       disabled_until = NULL,
                       last_error = NULL,
                       last_success_at = excluded.last_success_at,
                       total_jobs = source_state.total_jobs + excluded.total_jobs""",
                (source, _utcnow(), jobs_found),
            )
            self.conn.commit()

    def record_failure(
        self, source: str, error: str, max_failures: int = 3, disable_hours: int = 6
    ) -> bool:
        """Registra un fallo y activa el circuit breaker si corresponde.

        Returns:
            True si la fuente quedó desactivada temporalmente.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT consecutive_failures FROM source_state WHERE source = ?", (source,)
            ).fetchone()
            failures = (row["consecutive_failures"] if row else 0) + 1
            disabled_until: Optional[str] = None
            if failures >= max_failures:
                disabled_until = (
                    datetime.now(timezone.utc) + timedelta(hours=disable_hours)
                ).isoformat()
            self.conn.execute(
                """INSERT INTO source_state (source, consecutive_failures, disabled_until, last_error)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       consecutive_failures = excluded.consecutive_failures,
                       disabled_until = excluded.disabled_until,
                       last_error = excluded.last_error""",
                (source, failures, disabled_until, error[:500]),
            )
            self.conn.commit()
        return disabled_until is not None

    def disable_source(self, source: str, hours: int, reason: str) -> None:
        """Desactiva una fuente manualmente por N horas (p.ej. LinkedIn 429/999)."""
        until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with self._lock:
            self.conn.execute(
                """INSERT INTO source_state (source, consecutive_failures, disabled_until, last_error)
                   VALUES (?, 0, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       disabled_until = excluded.disabled_until,
                       last_error = excluded.last_error""",
                (source, until, reason[:500]),
            )
            self.conn.commit()

    # ---------------------------------------------------------------- cache IA
    def get_ai_cache(self, job_id: str) -> Optional[tuple[int, str]]:
        """Devuelve (score, motivo) del cache de IA para una oferta, si existe."""
        with self._lock:
            row = self.conn.execute(
                "SELECT ai_score, reason FROM ai_cache WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not row or row["ai_score"] is None:
            return None
        return int(row["ai_score"]), str(row["reason"] or "")

    def save_ai_cache(self, job_id: str, ai_score: int, reason: str, model: str) -> None:
        """Guarda el resultado de la IA para no re-evaluar la misma oferta."""
        with self._lock:
            self.conn.execute(
                """INSERT INTO ai_cache (job_id, ai_score, reason, model, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       ai_score = excluded.ai_score,
                       reason = excluded.reason,
                       model = excluded.model,
                       created_at = excluded.created_at""",
                (job_id, int(ai_score), reason[:400], model, _utcnow()),
            )
            self.conn.commit()

    # ------------------------------------------------------------- estadística
    def record_cycle(
        self,
        started_at: str,
        sources_ok: int,
        sources_failed: int,
        new_jobs: int,
        notified: int,
    ) -> None:
        """Guarda el resumen de un ciclo."""
        with self._lock:
            self.conn.execute(
                """INSERT INTO cycles (started_at, finished_at, sources_ok, sources_failed,
                                       new_jobs, notified)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (started_at, _utcnow(), sources_ok, sources_failed, new_jobs, notified),
            )
            self.conn.commit()

    def stats(self) -> dict[str, Any]:
        """Estadísticas globales para el CLI `--stats`."""
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]
            notified = self.conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE notified_at IS NOT NULL"
            ).fetchone()["c"]
            by_source = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT source, COUNT(*) AS jobs, SUM(notified_at IS NOT NULL) AS notified, "
                    "ROUND(AVG(score), 1) AS avg_score FROM jobs GROUP BY source ORDER BY jobs DESC"
                ).fetchall()
            ]
            last_cycles = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM cycles ORDER BY id DESC LIMIT 5"
                ).fetchall()
            ]
            sources = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM source_state ORDER BY source"
                ).fetchall()
            ]
            top = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT title, company, score, source, url, matched_skills FROM jobs "
                    "WHERE notified_at IS NOT NULL ORDER BY notified_at DESC LIMIT 10"
                ).fetchall()
            ]
        return {
            "total_jobs": total,
            "notified_jobs": notified,
            "by_source": by_source,
            "last_cycles": last_cycles,
            "source_state": sources,
            "last_notified": top,
        }

    def close(self) -> None:
        """Cierra la conexión."""
        with self._lock:
            self.conn.close()
