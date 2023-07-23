# DImROD - Decently-Impressive Residence Operation Device

**DImROD** is a collection of programs and scripts that make up my home server.
Somewhat intelligent and on its way to becoming the backbone of my smarthome,
DImROD is comprised of:

1. An Nginx HTTP server hosting a web interface
2. A Jellyfin media streaming server
3. Pihole for DNS-based ad blocking on my home network
4. A number of python services responsible for:
    * Interacting with WiFi lights/plugs
    * Monitoring the network
    * Manage and send me reminders
    * Interact with public APIs (weather, to-do list apps, etc.)
    * Processing remote commands via a Telegram bot
    * ...and more to come!

## File Layout

The top-level directories are:

* `http/` - Scripts for setting up Nginx, and the server's HTML/JS/CSS.
* `ifttt/` - IFTTT routines I've created that DImROD's services interact with.
* `jellyfin/` - Scripts for setting up a Jellyfin media server.
* `pihole/` - Scripts for setting up a Pihole DNS ad-blocking server.
* `scripts/` - Other general-purpose scripts.
* `services/` - My Python-based custom services.
* `cron/` - My cron jobs.

## DImROD Services

Within `services/` is the library code I implemented to create individual
services that provide me with some sort of functionality. Each service:

* Runs a machine-local process that periodically performs its job, *and*
* Exposes an HTTP API to my home network for communication (with built-in
  authentication).

These services are implemented in Python and will process authenticated requests
across my home network. Depending on the task, they might even talk with other
services via the HTTP APIs.

Currently, the services I've created are:

* **Lumen** - used to toggle various WiFi-connected lights and plugs around the house.
* **Warden** - scans the network to identify connected devices.
* **Gatekeeper** - the only service allowed access to incoming traffic outside the home network. Receives requests from my remote devices to dispatch jobs at home.
* **Telegram** - implements a Telegram chat bot, so I can chat with DImROD, set reminders, turn on/off lights, and interact with other parts of DImROD.
* **Notif** - implements a reminder system that, paired with the Telegram bot, allows for recurring or one-off reminders to be delivered to my phone.
* **Speaker** - uses my internal dialogue library to expose an interface for other services to "chat" with DImROD. Eventually, it'll be responsible not only for chatting, but parsing user dialogue and firing off jobs at home.
* **Nimbus** - uses public weather APIs to expose an interface for retrieving weather information for multiple locations.
* **Scribble** - somewhat underdeveloped, this service is used to interact with an API for interacting with my ToDO lists. Currently I'm using the TickTick mobile app.

## Future Plans

* Implement a conversation service used to:
    * Talk with DImROD
    * Instruct DImROD to interact with other services to accomplish things
    * Generally make me feel like Tony Stark
* Create microphone+speaker modules that allow me to speak with DImROD around
  the house.
   * Create a 2FA service that sends me pass phrases (”beetle fart” and other similar ridiculous, hard-to-guess sayings) to use as authentication for certain actions when speaking with DImROD via these voice modules. (Or, alternatively, find an open source way to identify individuals by their voices)
