"""Точка входа endpoint-агента: python -m agent.main"""

from __future__ import annotations

import logging

from agent import __version__
from agent.clipboard import detect_backend
from agent.config import AgentConfig
from agent.monitor import ClipboardMonitor

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("aigate.agent")


def main() -> None:
    config = AgentConfig.from_env()
    clipboard = detect_backend()
    monitor = ClipboardMonitor(config, clipboard)
    logger.info("AI-Gate Endpoint Agent v%s", __version__)
    try:
        monitor.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановка агента")


if __name__ == "__main__":
    main()
