"""Точка входа AI-Gate: сборка ядра и запуск HTTP-сервера."""

from __future__ import annotations

import logging

from gateway import __version__
from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.detectors.custom import CustomRuleRegistry
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.core.providers import MultiForwarder, ProviderConfig, ProviderRegistry
from gateway.core.semantic import SemanticGuard
from gateway.server import make_server

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("aigate")


def build_pipeline(settings: Settings) -> Pipeline:
    # Мультипровайдер (Э3): реестр в data/providers.json; при первом запуске
    # засеивается провайдером из AIGATE_PROVIDER_* (обратная совместимость).
    registry = ProviderRegistry(
        settings.providers_path,
        seed=ProviderConfig(
            name=settings.provider_name,
            kind="openai",
            base_url=settings.provider_base_url,
            api_key=settings.provider_api_key,
            model=settings.provider_model,
            timeout=settings.provider_timeout_seconds,
        ),
    )
    forwarder = MultiForwarder(registry)

    semantic_guard = None
    if settings.semantic_guard_mode != "off":
        semantic_guard = SemanticGuard(
            forwarder,
            mode=settings.semantic_guard_mode,
            provider=settings.semantic_guard_provider or None,
        )

    return Pipeline(
        policy_engine=PolicyEngine(settings.policy_path),
        mapping_store=MappingStore(
            encryption_key=settings.mapping_encryption_key,
            ttl_seconds=settings.mapping_ttl_seconds,
        ),
        forwarder=forwarder,
        audit=AuditLog(settings.audit_log_path),
        fail_closed=settings.fail_closed,
        custom_rules=CustomRuleRegistry(settings.detector_rules_path),
        semantic_guard=semantic_guard,
    )


def main() -> None:
    settings = Settings.from_env()
    pipeline = build_pipeline(settings)
    server = make_server(pipeline, settings)
    logger.info(
        "AI-Gate v%s: on-prem шлюз запущен на %s:%s "
        "(fail_closed=%s, провайдер=%s, semantic_guard=%s)",
        __version__,
        settings.host,
        settings.port,
        settings.fail_closed,
        pipeline.forwarder.provider_name,
        settings.semantic_guard_mode,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка шлюза")
        server.shutdown()


if __name__ == "__main__":
    main()
