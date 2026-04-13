# Running and Debugging DImROD Services

This guide covers how to set up, configure, run, and debug DImROD services.

## Prerequisites

DImROD requires the following system packages:

* `python3` — Python 3 interpreter
* `python3-pip` — Python package manager
* `python3-venv` — Python virtual environment support
* `nmap` — Network scanner (required by the Warden service)

Install these with the provided script:

```bash
bash scripts/install-dependencies.sh
```

## Environment Setup

### Virtual Environments

Each service runs inside its own Python virtual environment. You do not need to create these manually — the `run-service.sh` script handles venv creation and dependency installation automatically.

When a service starts, `run-service.sh`:

1. Creates a virtual environment at `<service_dir>/.venv/` (if it doesn't already exist)
2. Installs the shared library dependencies from `services/lib/requirements.txt`
3. Installs any service-specific dependencies from `<service_dir>/requirements.txt` (if present)
4. Activates the venv and launches the service

> **Note:** The `ServiceCLI` enforces that services run inside a virtual environment. If you try to run a service outside of a venv, it will refuse to start.

### Dependencies

Shared dependencies are pinned in `services/lib/requirements.txt` and include:

| Package | Purpose |
|---------|---------|
| `flask` | HTTP API framework (Oracle) |
| `gevent` | WSGI server for production |
| `PyJWT` | JWT authentication |
| `openai` | LLM integration |
| `todoist-api-python` | Todoist task management |
| `google-auth`, `google-api-python-client` | Google Calendar |
| `wyze-sdk` | Wyze smart home devices |
| `lifxlan` | LIFX smart lighting (LAN) |
| `ynab` | YNAB budgeting API |
| `newsapi-python` | News headlines |
| `garminconnect` | Garmin fitness data |
| `geopy`, `timezonefinder`, `pytz` | Geocoding and timezones |
| `python-dateutil` | Flexible date/time parsing |
| `openpyxl` | Excel export |

Some services have their own `requirements.txt` files for additional dependencies:

* **Telegram:** `pyTelegramBotAPI` — Telegram Bot API wrapper
* **Warden:** `ipaddress`, `mac-vendor-lookup` — Network device identification

## Configuration

### Config File Structure

Every service is configured via a config file. Both YAML and JSON formats are supported, but **YAML is the preferred format** for new configurations. YAML configs use the `.yaml` extension and JSON configs use the `.json` extension.

Config files follow a consistent structure with shared and service-specific fields.

**Common fields** (present in every service config):

```yaml
service_name: my_service
service_log: stdout
msghub_name: dimrod-my-service
oracle:
  addr: 0.0.0.0
  port: 5000
  log: stdout
  auth_cookie: dimrod_auth
  auth_secret: your-jwt-secret
  auth_users:
    - username: admin
      password: changeme
      privilege: 0
```

| Field | Description |
|-------|-------------|
| `service_name` | Unique identifier for the service |
| `service_log` | Log destination (`"stdout"` or a file path) |
| `msghub_name` | ntfy.sh topic name for push notifications |
| `oracle.addr` | Address the HTTP API binds to |
| `oracle.port` | Port the HTTP API listens on |
| `oracle.auth_cookie` | Name of the JWT cookie |
| `oracle.auth_secret` | Secret key for signing JWTs |
| `oracle.auth_users` | List of authorized users with credentials |

**Optional Oracle fields:**

| Field | Description |
|-------|-------------|
| `oracle.debug` | Set to `true` to run Flask's dev server instead of gevent |
| `oracle.auth_exptime` | JWT expiration time (seconds) |
| `oracle.https_cert` | Path to HTTPS certificate file |
| `oracle.https_key` | Path to HTTPS private key file |

### Template vs. Deployed Configs

Each service directory contains two types of config files:

* **Template configs** (e.g., `lumen.yaml`) — Contain placeholder values and serve as documentation for the expected structure. These are checked into version control.
* **Deployed configs** (e.g., `cwshugg_lumen.yaml`) — Contain real API keys, credentials, and deployment-specific settings. These are gitignored.

To set up a new deployment, copy a template config and fill in real values:

```bash
cp services/lumen/lumen.yaml services/lumen/mydeployment_lumen.yaml
# Edit the new file with your settings
```

### Oracle Session Configs

When one service needs to talk to another, it includes an `OracleSessionConfig` block in its config. This specifies how to connect and authenticate:

```yaml
speaker:
  addr: localhost
  port: 5001
  auth_username: admin
  auth_password: changeme
```

## Starting Services

### Manual Launch

Use `scripts/run-service.sh` to start a service manually:

```bash
bash scripts/run-service.sh services/lumen/lumen.py --config services/lumen/myconfig_lumen.yaml --oracle
```

**Arguments:**

* **First argument:** Path to the service's main Python file
* **`--config`:** Path to the service's config file (required; accepts `.yaml` or `.json`)
* **`--oracle`:** Flag to enable the HTTP API server (optional, but almost always needed)

### The ServiceCLI Pattern

Under the hood, every service uses `ServiceCLI` to handle startup. Each service's main Python file ends with code like:

```python
cli = ServiceCLI(config=LumenConfig, service=LumenService, oracle=LumenOracle)
cli.run()
```

`ServiceCLI.run()` performs these steps:

1. Parses `--config` and `--oracle` arguments
2. Loads the config file (YAML or JSON) into the service's config class
3. Verifies the process is running inside a virtual environment
4. Creates the `Service` instance (background worker thread)
5. Optionally creates the `Oracle` instance (HTTP API thread)
6. Installs a `SIGINT` handler for graceful shutdown
7. Starts the Service thread, then the Oracle thread
8. Blocks until both threads complete

### Systemd Deployment

For production, each service runs as a systemd unit. Service unit files are located at `services/<name>/cwshugg_<name>.service`.

**Unit file structure:**

```ini
[Unit]
Description=DImROD Service - <Name>
Documentation=https://github.com/cwshugg/dimrod
After=syslog.target network.target

[Service]
Type=simple
ExecStart=/path/to/scripts/run-service.sh /path/to/services/<name>/<name>.py --config /path/to/config.yaml --oracle

[Install]
WantedBy=multi-user.target
```

**Installing services with systemd:**

```bash
# Install one or more services
sudo bash scripts/install-services.sh lumen speaker telegram

# This copies the .service file to /etc/systemd/system/dimrod_<name>.service,
# runs daemon-reload, and enables+starts the service.
```

**Managing services:**

```bash
# Restart a running service (or start it if stopped)
bash scripts/restart-service.sh lumen

# Check status
systemctl status dimrod_lumen

# View logs
journalctl -u dimrod_lumen -f
```

All systemd service names follow the pattern `dimrod_<service>`.

## Debugging

### Debug Mode

Set `"debug": true` in the Oracle config to run Flask's built-in development server instead of the gevent WSGI server. This enables:

* Auto-reload on code changes
* Detailed error pages
* More verbose request logging

```yaml
oracle:
  debug: true
```

> **Warning:** Never use debug mode in production — it is insecure and not performant.

### Logging

Each service has two loggers:

* **Service log** — Logs from the background worker thread (configured via `service_log`)
* **Oracle log** — Logs from the HTTP API thread (configured via `oracle.log`)

Both default to `stdout`. To log to files, set these fields to file paths in the config:

```yaml
service_log: /var/log/dimrod/lumen.log
oracle:
  log: /var/log/dimrod/lumen-oracle.log
```

Log entries are prefixed with timestamps and the logger name, e.g.:

```
[2024-01-15 10:30:45] [lumen] Starting service...
```

### Push Notifications

Every service publishes important events to its ntfy.sh channel (configured via `msghub_name`). Subscribe to a service's notifications at:

```
https://ntfy.sh/<msghub_name>
```

### Common Issues

For common issues and troubleshooting, see [Common Issues](common-issues.md).
