# MIDOS - Moderately-Intelligent Dwelling Operating System

**MIDOS** is a collection of programs and scripts that make up my home server.
Somewhat intelligent and on its way to becoming the backbone of my smarthome,
MIDOS is comprised of:

1. An Nginx HTTP server hosting a web interface
2. A Jellyfin media streaming server
3. Pihole for DNS-based ad blocking on my home network
4. A number of python services responsible for:
    * Interacting with WiFi lights/plugs
    * Monitoring the network
    * ...and more to come!

## File Layout

The top-level directories are:

* `http/` - Scripts for setting up Nginx, and the server's HTML/JS/CSS.
* `ifttt/` - IFTTT routines I've created that MIDOS's services interact with.
* `jellyfin/` - Scripts for setting up a Jellyfin media server.
* `pihole/` - Scripts for setting up a Pihole DNS ad-blocking server.
* `scripts/` - Other general-purpose scripts.
* `services/` - My Python-based custom services.

## MIDOS Services

Within `services/` is the library code I implemented to create individual
services that provide me with some sort of functionality. Each service:

* Runs a machine-local process that periodically performs its job, *and*
* Exposes a HTTP API to my home network for communication (with built-in
  authentication).

These services are implemented in Python and will process authenticated requests
across my home network. Depending on the task, they might even talk with other
services via the HTTP APIs.

## Future Plans

* Implement a weather service that uses [this API](https://www.weather.gov/documentation/services-web-api)
* Implement a grocery list management service 
* Implement a conversation service used to:
    * Talk with MIDOS
    * Instruct MIDOS to interact with other services to accomplish things
    * Generally make me feel like Tony Stark
* Create microphone+speaker modules that allow me to speak with MIDOS around
  the house.

