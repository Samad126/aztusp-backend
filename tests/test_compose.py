"""Every Compose service comes back after a reboot or power cut. Reads docker-compose.yml only; no database or Docker needed."""

from pathlib import Path

import yaml

COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"

# Docker starts these again when the daemon starts after a reboot. The default, `no`, leaves the container stopped.
RESTART_AFTER_REBOOT = {"unless-stopped", "always"}


def test_every_service_restarts_after_a_reboot():
    services = yaml.safe_load(COMPOSE_FILE.read_text())["services"]

    assert {"db", "api", "watcher"} <= services.keys()
    for name, service in services.items():
        assert service.get("restart") in RESTART_AFTER_REBOOT, f"{name} stays stopped after a reboot"
