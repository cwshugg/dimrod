# This module implements the mileage check-in TaskJob for the Gearhead service.
# It periodically sends Telegram messages to ask the user for the current
# mileage of each configured vehicle, then records the response via Gearhead's
# POST /mileage endpoint.

# Imports
import os
import re
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from tasks.gearhead.base import TaskJob_Gearhead


class TaskJob_Gearhead_MileageCheckin(TaskJob_Gearhead):
    """A TaskJob that periodically asks the user for the current mileage of
    each vehicle tracked by the Gearhead service.

    For each vehicle, a Telegram question is sent asking the user to reply with
    the current odometer reading. The reply is parsed as a float and recorded
    via Gearhead's ``POST /mileage`` endpoint. Vehicles are processed
    sequentially — one question at a time — so the user is never overwhelmed
    with multiple prompts at once.
    """
    def init(self):
        """Initialization hook.

        Calls the parent ``init()`` to load config and set defaults, then
        applies the configured check-in interval.
        """
        super().init()

        # apply the configured check-in interval (converted from days to
        # seconds)
        self.refresh_rate = 86400 * self.gearhead_config.checkin_interval_days

    def update(self):
        """Main update function.

        1. Fetches all vehicles from the Gearhead service.
        2. For each vehicle, sends a Telegram question asking for the mileage.
        3. Waits for the user's reply, parses it as a number.
        4. Records the mileage via Gearhead's API.
        5. Sends a confirmation message.

        Returns ``True`` if at least one mileage reading was successfully
        recorded; ``False`` otherwise.
        """
        chat_id = self.gearhead_config.telegram_chat_id

        # ----- Step 1: Connect to Gearhead ----- #
        try:
            gearhead = self.get_gearhead_session()
        except Exception as e:
            self.log("Failed to connect to Gearhead service: %s" % e)
            return False

        # ----- Step 2: Fetch vehicles from Gearhead ----- #
        try:
            r = gearhead.get("/vehicles")
            assert gearhead.get_response_success(r), \
                   "Gearhead reteurned a failure: %s" % \
                   gearhead.get_response_message(r)
            vehicles = gearhead.get_response_json(r)
        except Exception as e:
            self.log("Failed to fetch vehicles from Gearhead: %s" % e)
            return False

        if not vehicles or len(vehicles) == 0:
            self.log("No vehicles configured in Gearhead. Nothing to do.")
            return False

        self.log("Retrieved %d vehicle(s) from Gearhead." % len(vehicles))

        # ----- Step 3-5: Ask about each vehicle sequentially ----- #
        successful_recordings = 0
        for vehicle in vehicles:
            vehicle_id = vehicle.get("id", "unknown")
            vehicle_name = self._get_vehicle_display_name(vehicle)

            # send a question asking for the mileage
            question = "🚗 What is the current mileage for <b>%s</b>?" % vehicle_name
            self.log("Asking user for mileage of '%s'..." % vehicle_name)

            try:
                convo = self.send_question(chat_id, question)
            except Exception as e:
                self.log("Failed to send Telegram question for '%s': %s" %
                         (vehicle_name, e))
                continue

            # wait for the user's reply
            reply = self.wait_for_reply(convo)
            if reply is None:
                self.log("No reply received for '%s'. Skipping." % vehicle_name)
                msg = "⏰ No reply received for <b>%s</b>. Skipping this vehicle." % vehicle_name
                self._try_send_message(chat_id, msg)
                continue

            # parse the reply as a mileage number
            mileage = self._parse_mileage(reply)
            if mileage is None:
                self.log("Invalid mileage reply for '%s': '%s'" %
                         (vehicle_name, reply))
                msg = "⚠️ Sorry, I couldn't understand \"%s\" as a mileage " \
                      "number for <b>%s</b>. Skipping this vehicle." % \
                      (reply.strip(), vehicle_name)
                self._try_send_message(chat_id, msg)
                continue

            # record the mileage via Gearhead
            try:
                payload = {
                    "vehicle_id": vehicle_id,
                    "mileage": mileage,
                }
                r = gearhead.post("/mileage", payload=payload)
                assert gearhead.get_response_success(r), \
                       "Gearhead rejected mileage update: %s" % \
                       gearhead.get_response_message(r)
            except Exception as e:
                self.log("Failed to record mileage for '%s': %s" %
                         (vehicle_name, e))
                msg = "❌ Failed to record mileage for <b>%s</b>: %s" % \
                      (vehicle_name, e)
                self._try_send_message(chat_id, msg)
                continue

            # send confirmation
            confirm_msg = "✅ Recorded <b>%.1f</b> miles for <b>%s</b>." % \
                          (mileage, vehicle_name)
            self._try_send_message(chat_id, confirm_msg)
            self.log("Recorded %.1f miles for '%s'." % (mileage, vehicle_name))
            successful_recordings += 1

        # return True if we recorded at least one mileage
        return successful_recordings > 0

    # ------------------------------- Helpers -------------------------------- #
    def _get_vehicle_display_name(self, vehicle: dict) -> str:
        """Builds a human-readable display name for a vehicle.

        Prefers the first nickname if available, otherwise falls back to
        ``{year} {manufacturer}``.
        """
        nicknames = vehicle.get("nicknames", [])
        if nicknames and len(nicknames) > 0:
            return nicknames[0]

        year = vehicle.get("year", "")
        manufacturer = vehicle.get("manufacturer", "")
        return ("%s %s" % (year, manufacturer)).strip()

    def _parse_mileage(self, reply):
        """Parse the first number (int or float) from a text string.

        Uses regex to extract the first numeric value found in the reply,
        so the user can type natural-language responses like
        ``"about 45,000 miles"`` and still have the mileage parsed correctly.

        Handles formats like:

        - ``"45000"``
        - ``"45,000"``
        - ``"45000.5"``
        - ``"45,230.5"``
        - ``"about 45000 miles"``
        - ``"it's at 45,230.5 mi"``

        Returns the parsed float, or ``None`` if no number is found.
        """
        if reply is None:
            return None
        # Match numbers with optional commas and decimal point
        match = re.search(r'[\d,]+\.?\d*', reply)
        if match is None:
            return None
        # Remove commas and convert to float
        try:
            value = float(match.group().replace(',', ''))
        except (ValueError, TypeError):
            return None
        if value < 0:
            return None
        return value

    def _try_send_message(self, chat_id: str, text: str):
        """Attempts to send a Telegram message, logging any failure without
        raising an exception.
        """
        try:
            self.send_message(chat_id, text)
        except Exception as e:
            self.log("Failed to send Telegram message: %s" % e)
