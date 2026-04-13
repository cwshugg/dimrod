# Rambler — Flight Scraper

Rambler is a scheduled flight price scraper that monitors airfare prices for configured trips.

## Purpose

* Configure trip parameters (airports, date ranges, passengers)
* Periodically scrape flight prices from travel websites
* Track and report on price changes

## Architecture

`RamblerService` runs a continuous loop that:

1. Iterates through configured trip definitions
2. Generates embark/return date pairs based on trip timing configurations
3. Opens a headless browser via Selenium to scrape flight prices from Kayak
4. Sleeps between requests with randomized delays to avoid rate limiting

The service is entirely background-driven with no Oracle API endpoints.

## Oracle Endpoints

None — Oracle endpoints are marked as TODO in the codebase.

## NLA Endpoints

None.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `trips` | `list[TripConfig]` | Trip definitions |
| `flight_scraper` | `str` | Scraper backend name (e.g., `"kayak"`) |
| `flight_scraper_result_max` | `str` | Maximum results to collect per search |
| `flight_scraper_delay_min` | `int` | Minimum delay between scrapes (seconds) |
| `flight_scraper_delay_max` | `int` | Maximum delay between scrapes (seconds) |
| `refresh_rate` | `int` | Main loop tick interval |

### Trip Configuration

Trips are defined with nested config objects:

**`TripConfig`:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Trip name (required) |
| `timing` | `TripTimeConfig` | Date/time parameters (required) |
| `flight` | `TripFlightConfig` | Flight details (optional) |
| `description` | `str` | Trip description (optional) |

**`TripFlightConfig`:**

| Field | Type | Description |
|-------|------|-------------|
| `airport_embark_depart` | `str` | Departure airport for outbound flight (required) |
| `airport_embark_arrive` | `str` | Arrival airport for outbound flight (required) |
| `airport_return_depart` | `str` | Departure airport for return flight (required) |
| `airport_return_arrive` | `str` | Arrival airport for return flight (required) |
| `passengers_adult` | `int` | Number of adult passengers (required) |
| `passengers_child` | `int` | Number of child passengers (default: `0`) |

**`TripTimeConfig`:**

| Field | Type | Description |
|-------|------|-------------|
| `number_of_nights` | `list` | List of trip durations in nights (required) |
| `departure_weekdays` | `list` | Preferred departure weekdays, Sunday=0 through Saturday=6 (required). Accepts integers or abbreviation strings (e.g., `"mo"`, `"fr"`) |
| `departure_months` | `list` | Preferred departure months, 1–12 or abbreviation strings (e.g., `"jan"`) (optional) |
| `departure_years` | `list` | Preferred departure years (optional; defaults to current year + 1 ahead) |
| `lookahead_days` | `int` | Number of days ahead to search (default: `7`) |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`
* **Python packages:** Selenium (for headless browser scraping)
* **External websites:** Kayak (flight price data)
* **Other services:** None

## Notable Details

* Uses headless Selenium (Chrome/Chromium) for web scraping
* Randomized delays between requests help avoid rate limiting
* The cron job `cron/flights.py` provides additional flight search functionality
* This service has no HTTP API — it operates entirely as a background scraper
