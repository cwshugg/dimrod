# Warden — Network Monitor

Warden scans the local network to detect and track connected devices.

## Purpose

* Periodically sweep the network to discover connected devices
* Identify devices by MAC address, IP address, and vendor
* Provide a list of known (pre-configured) and discovered devices via HTTP

## Architecture

Warden performs network discovery using a combination of system tools:

1. **`nmap`** — Scans the local subnet for active hosts
2. **`ping`** — Probes individual hosts to confirm availability
3. **ARP tables** — Queries ARP (`arp` / `ip neigh`) for MAC address resolution
4. **MAC vendor lookup** — Maps MAC addresses to manufacturer names using a cached vendor database

On startup, the service performs a configurable number of initial sweeps to build its device cache. It then continues scanning at a regular interval, updating the cache with newly discovered or departed devices. Each device's `last_seen` timestamp is tracked.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/known_devices` | List all pre-configured known devices |
| `GET` | `/devices` | List all currently detected devices |

### `/devices` Request Fields

| Field | Description |
|-------|-------------|
| `tags` | Filter devices by tags (optional, JSON body) |

## NLA Endpoints

None.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `known_devices` | `list[KnownDeviceConfig]` | Pre-configured device definitions |
| `refresh_rate` | `int` | Seconds between network scans |
| `ping_timeout` | `int` | Ping timeout (seconds) |
| `ping_tries` | `int` | Number of ping attempts per host |
| `sweep_threshold` | `int` | Threshold for sweep triggering |
| `initial_sweeps` | `int` | Number of sweeps on startup |
| `mac_vendor_cache_path` | `str` | Path to MAC vendor database file |

### Known Device Configuration

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable device name |
| `macaddrs` | `list[str]` | Known MAC addresses for the device |
| `tags` | `list[str]` | Tags for filtering |

### Device Data Model

Discovered devices include:

| Class | Fields |
|-------|--------|
| `DeviceHardwareAddress` | `macaddr`, `vendor` |
| `DeviceNetworkAddress` | `ipaddr` |
| `Device` | `known_device`, `hw_addr`, `net_addr`, `last_seen` |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`
* **Python packages:** `ipaddress`, `mac-vendor-lookup` (service-specific dependencies)
* **System tools:** `nmap`, `ping`, `arp`, `ip`
* **Other services:** None

## Notable Details

* The MAC vendor database (`.mac_vendors.txt`) is cached locally and refreshed periodically
* Known devices are matched by MAC address — a discovered device with a matching MAC is linked to its known device entry
* The cron job `cron/whos_online.py` uses Warden to detect device online/offline changes and send Telegram notifications
