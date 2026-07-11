"""Точка входа AI-Gate: сборка ядра и запуск HTTP-сервера."""

from __future__ import annotations

import logging

from gateway import __version__
from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.forwarder import Forwarder
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.server import make_server

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("aigate")


def build_pipeline(settings: Settings) -> Pipeline:
    return Pipeline(
        policy_engine=PolicyEngine(settings.policy_path),
        mapping_store=MappingStore(
            encryption_key=settings.mapping_encryption_key,
            ttl_seconds=settings.mapping_ttl_seconds,
        ),
        forwarder=Forwarder(settings),
        audit=AuditLog(settings.audit_log_path),
        fail_closed=settings.fail_closed,
    )


def main() -> None:
    settings = Settings.from_env()
    pipeline = build_pipeline(settings)
    server = make_server(pipeline, settings)
    logger.info(
        "AI-Gate v%s: on-prem шлюз запущен на %s:%s (fail_closed=%s, провайдер=%s)",
        __version__,
        settings.host,
        settings.port,
        settings.fail_closed,
        settings.provider_name,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка шлюза")
        server.shutdown()


if __name__ == "__main__":
    main()
