# Nimbus — Weather Service

Nimbus provides weather forecast lookups using the US National Weather Service API.

## Purpose

* Fetch weather forecasts by geographic coordinates or saved location names
* Geocode location names to coordinates using `geopy`
* Parse and return structured forecast data

## Architecture

On startup, `NimbusService` pre-geocodes all configured locations (resolving addresses to latitude/longitude coordinates). The service itself has no background worker loop — all functionality is exposed through Oracle endpoints.

Weather data is fetched from the [National Weather Service API](https://api.weather.gov) in two steps:

1. Look up the NWS grid point for the given coordinates
2. Fetch the forecast for that grid point

Forecasts are returned as a list of `Forecast` objects, each representing a time period (e.g., "Tonight", "Wednesday").

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/weather/bylocation` | Get forecast by latitude/longitude |
| `POST` | `/weather/byname` | Get forecast by saved location name |

### `/weather/bylocation` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `location` | Yes | Location JSON with `latitude` and `longitude` |
| `when` | No | Epoch seconds for a specific forecast period |

### `/weather/byname` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Name of a saved location from the config |
| `when` | No | Epoch seconds for a specific forecast period |

## NLA Endpoints

None.

## Forecast Data Model

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Forecast period name (e.g., "Tonight") |
| `description_short` | `str` | Brief forecast summary |
| `description_long` | `str` | Detailed forecast description |
| `temperature_value` | `int` | Temperature reading |
| `temperature_unit` | `str` | Temperature unit (e.g., `"F"`) |
| `wind_speed` | `str` | Wind speed |
| `wind_direction` | `str` | Wind direction |
| `time_start` | `str` (converted to `datetime`) | Forecast period start. Stored as `str` in Uniserdes; parsed to `datetime` via `dateutil.parser.parse()` in `parse_json()` |
| `time_end` | `str` (converted to `datetime`) | Forecast period end. Same conversion as `time_start` |

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `locations` | `list[Location]` | Named locations with addresses and/or coordinates |
| `geopy_geocoder` | `str` | Geocoder service name |
| `geopy_user_agent` | `str` | User agent string for geocoding requests |
| `api_address` | `str` | NWS API base URL |
| `api_user_agent` | `str` | User agent string for NWS API requests |

### Location Configuration

> **Note:** Nimbus defines its own `Location` class in `services/nimbus/location.py` (a `Uniserdes` subclass), which is separate from the `Location` class in `lib/lu.py`. The Nimbus version includes a `name` field for lookup-by-name functionality.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Location name (used with `/weather/byname`) |
| `address` | `str` | Street address (geocoded to coordinates) |
| `longitude` | `float` | Longitude (optional if address is provided) |
| `latitude` | `float` | Latitude (optional if address is provided) |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`
* **Python packages:** `geopy` (shared dependency)
* **External APIs:** [api.weather.gov](https://api.weather.gov) (US National Weather Service)
* **Other services:** None

## Notable Details

* Only supports US locations (the National Weather Service API is US-only)
* The cron job `cron/weather.py` uses Nimbus to check for rain/snow and send alerts via Telegram
* The `when` parameter allows fetching a forecast for a specific future time period
