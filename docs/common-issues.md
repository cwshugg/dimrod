# Common Issues

This document covers common problems and troubleshooting steps for DImROD services.

## Service refuses to start — "not running in a virtual environment"

The `ServiceCLI` requires that services run inside a Python venv. Use `scripts/run-service.sh` to start services, which handles venv setup automatically. If running manually, activate the venv first:

```bash
source services/lumen/.venv/bin/activate
python3 services/lumen/lumen.py --config config.yaml --oracle
```

## Authentication failures between services

If one service cannot communicate with another, verify that:

* The target service is running and its Oracle is accepting connections
* The `OracleSessionConfig` in the calling service's config has the correct address, port, username, and password
* The target service's `auth_users` list includes matching credentials

## LIFX lights not responding

LIFX uses LAN-based discovery. Ensure the server and LIFX bulbs are on the same network subnet. The `LIFX` class has configurable retry attempts and delays in the `lifx_config` block.

## Wyze devices timing out

Wyze API keys expire periodically. The Taskmaster service includes a task that automatically refreshes Wyze API keys. If Wyze stops working, check that Taskmaster is running and the key refresh task is executing successfully.
