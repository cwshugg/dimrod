# Implements the /remind bot command.

# Imports
import os
import sys
import re
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from notif.reminder import Reminder
import lib.dtu as dtu
from menu import Menu


# Stable key under which the reminder-cancel menu action is registered with the
# Telegram service (see `TelegramService.register_menu_action`).
REMINDER_CANCEL_ACTION_KEY = "reminder_cancel"


def cancel_reminder(service, rem_id: str, chat_id, notify_success: bool = True) -> bool:
    """Core reminder-cancellation logic.

    Logs into notif and posts to `/reminder/delete` for `rem_id`, reporting
    progress/errors back to `chat_id`. Returns True on success and False on any
    failure. This is the shared core used by both the `/remind cancel. <id>`
    text command (via `delete_reminder`) and the inline "❌ Cancel" button (via
    `reminder_cancel_action`); it deliberately takes only `service`, `rem_id`,
    and `chat_id` so it can run without a fabricated telegram message.

    When `notify_success` is True (the default, used by the text command), a
    "Successfully deleted the reminder..." confirmation is sent to `chat_id` on
    success. The button handler passes `notify_success=False` because its
    "Cancelled ✅" button update is sufficient confirmation. Failure/error
    messages are always sent regardless of this flag.
    """
    # create a HTTP session with notif
    session = OracleSession(service.config.notif)
    try:
        r = session.login()
        if r.status_code != 200 or not session.get_response_success(r):
            service.send_message(chat_id,
                                 "Sorry, I couldn't log into Notif.")
            return False
    except Exception:
        service.send_message(chat_id,
                             "Sorry, I couldn't reach Notif. "
                             "It might be offline.")
        return False

    # send the deletion request
    payload = {
        "reminder_id": rem_id,
    }
    rem = None
    try:
        r = session.post("/reminder/delete", payload=payload)
        if r.status_code != 200 or not session.get_response_success(r):
            service.send_message(chat_id,
                                 "Sorry, I couldn't delete the reminder. (%s)" %
                                 session.get_response_message(r))
            return False

        rem = Reminder.from_json(session.get_response_json(r))
    except Exception as e:
        service.send_message(chat_id,
                             "Sorry, I couldn't delete the reminder. (%s)" % e)
        return False

    # send a success message
    if notify_success:
        service.send_message(chat_id,
                             "Successfully deleted the reminder:\n\n<b>%s</b> - %s" %
                             (rem.title, rem.message),
                             parse_mode="HTML")
    return True


def delete_reminder(service, message, rem_id: str):
    """Cancels a reminder in response to the `/remind cancel. <id>` text command.

    Thin wrapper around `cancel_reminder` that supplies the originating chat id
    from the incoming `message`.
    """
    return cancel_reminder(service, rem_id, message.chat.id)


def reminder_cancel_action(service, call, menu, option, context):
    """Menu-action handler for the inline "❌ Cancel" button.

    Registered under `REMINDER_CANCEL_ACTION_KEY`. Cancels the reminder named in
    `context["reminder_id"]` and updates the button to a terminal
    "Cancelled ✅" state. Idempotent: once the option has been marked cancelled
    (persisted in `context`), a second press is a safe no-op.
    """
    # idempotency guard: a previously-successful cancel marks the context so a
    # double-press does nothing (and does not re-hit notif).
    if context.get("cancelled"):
        return

    rem_id = context.get("reminder_id")
    if rem_id is None:
        return

    # the reminder should be cancelled in (and feedback sent to) the chat the
    # menu message lives in
    chat_id = menu.telegram_msg_info.chat.id
    if not cancel_reminder(service, rem_id, chat_id, notify_success=False):
        # leave the button intact so the user can retry; the failure reason was
        # already reported by `cancel_reminder`.
        return

    # mark the option as terminal, persist, and update the button text
    context["cancelled"] = True
    option.action_context = context
    option.title = "Cancelled ✅"
    service.update_menu(menu.telegram_msg_info.chat.id,
                        menu.telegram_msg_info.id,
                        menu)
    service.menu_db.save_menu(menu)


# =================================== Main =================================== #
def command_remind(service, message, args: list):
    if len(args) < 2:
        msg = "🔔 <b>Usage:</b> <code>/remind &lt;time&gt;. &lt;message&gt;</code>\n\n" \
              "<b>Examples:</b>\n" \
              "  <code>/remind 1d 3h. Take out the trash!</code>\n" \
              "  <code>/remind 30m. Check the oven</code>\n" \
              "  <code>/remind Friday 9am. Weekly meeting</code>\n\n" \
              "<b>Delete a reminder:</b>\n" \
              "  <code>/remind cancel. &lt;reminder_id&gt;</code>\n\n" \
              "Separate the time and message with a period (<code>.</code>).\n" \
              "You can also reply to a message with <code>/remind &lt;time&gt;.</code> to be reminded of it."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # find where a "." appears first in the arguments. This is where we'll
    # separate datetime and message
    all_args = " ".join(args[1:])
    first_dot = all_args.index(".") if "." in all_args else len(all_args)
    pieces = [all_args]
    if first_dot >= 0 and len(all_args) >= first_dot + 1:
        pieces = [all_args[:first_dot], all_args[first_dot + 1:]]
    dt_args = pieces[0].split()

    # instead of keywords, was a keyword used to indicate that a reminder
    # should be cancelled/deleted?
    if pieces[0].lower().strip() in ["cancel", "delete", "remove"]:
        # make sure enough arguments were provided
        if len(pieces) < 2:
            msg = "To delete a reminder, you must specify its reminder ID after the period.\n\n" \
                  "For example:\n" \
                  "<code>/remind cancel. 123abc456def</code>"
            service.send_message(message.chat.id, msg, parse_mode="HTML")
            return

        # invoke the deletion helper function
        return delete_reminder(service, message, pieces[1].strip())

    # depending on what we found above, set the args to empty, or extract the
    # text appearing after the first "." in the *original* message (*not* the
    # args). Why? So we can preserve newlines and other whitespace elements
    # that were chopped up when the args were formed.
    msg = None
    if len(pieces) >= 2:
        reminder_text_begin = message.text.index(".")
        reminder_text = message.text[reminder_text_begin + 1:].strip()
        msg = reminder_text

    # if the message is in reply to another (a GENUINE user reply, NOT the
    # forum-topic auto-threading service message), we'll use the original
    # message's text as this reminder's message. Only overwrite `msg` when the
    # replied-to message actually has text, so that replying to a photo/sticker
    # (text is None) does not erase a validly-parsed after-period message.
    reply = service._genuine_reply(message)
    is_reply = reply is not None
    if is_reply and reply.text is not None:
        msg = reply.text

    # if we're missing either group of args, send back an error
    if len(dt_args) == 0:
        msg = "You need to specify some sort of date/time indicator " \
              "<i>before</i> the period in your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return
    if msg is None:
        msg = "You need to specify a message <i>after</i> the period in " \
              "your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # parse the arguments as a reminder
    dt = dtu.parse_datetime(dt_args)
    if dt is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't parse a date or time from your message.")
        return

    # parse_datetime returns a tz-aware datetime when the user typed a tz code
    # (e.g. "9am PT"), or a naive server-local datetime otherwise. Keep the
    # originally-parsed datetime for DISPLAY (so we can echo the user's own
    # time + zone), and separately derive a server-local naive datetime for the
    # PAYLOAD, since notif fires triggers against its own naive server-local
    # wall clock.
    #   * display_dt: what we show the user (their tz-aware time, or naive
    #     server-local time when no tz code was given).
    #   * trigger_dt: the naive server-local wall-clock time notif fires at.
    display_dt = dt
    trigger_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo is not None else dt

    # create a HTTP session with notif
    session = OracleSession(service.config.notif)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Notif. "
                             "It might be offline.")
        return

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Notif.")
        return
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Notif. "
                             "(%s)" % session.get_response_message(r))
        return

    # build the telegram target for this reminder: the originating chat, plus
    # the forum topic (message_thread_id) when the command came from one so the
    # reminder fires back into the same thread.
    telegram_target = {"chat": str(message.chat.id)}
    if getattr(message, "message_thread_id", None) is not None:
        telegram_target["topic"] = str(message.message_thread_id)

    # create the reminder by talking to notif's oracle
    payload = {
        "title": "" if is_reply else "🔔",
        "message": msg,
        "send_telegrams": [telegram_target],
        "trigger": {
            "years":   [trigger_dt.year],
            "months":  [trigger_dt.month],
            "days":    [trigger_dt.day],
            "hours":   [trigger_dt.hour],
            "minutes": [trigger_dt.minute]
        }
    }
    try:
        r = session.post("/reminder/create", payload=payload)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. (%s)" % e)
        return

    # check the reminder-creation response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. "
                             "Notif responded with a %d status code." %
                             r.status_code)
        return
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. (%s)" %
                             session.get_response_message(r))

    # extract the reminder ID string to send back in the response message
    rdata = OracleSession.get_response_json(r)
    rem_id = rdata["id"] if "id" in rdata else "(could not find reminder ID)"

    # report a success. Build the confirmation from `display_dt` so we echo the
    # time/zone the USER specified rather than the server-converted time.
    if display_dt.tzinfo is not None:
        # The user typed a tz code: show their own local time and zone
        # abbreviation. zoneinfo yields the correct DST-aware abbrev (e.g. MDT
        # in summer, MST in winter), and we use display_dt's own date too.
        tz_name = display_dt.tzname()
    else:
        # No tz code: default to server-local time. `trigger_dt` is the naive
        # server-local datetime; calling `.astimezone()` on it treats it as
        # system-local, yielding the DST-correct server tz abbreviation.
        tz_name = trigger_dt.astimezone().tzname()
    trigger_str = display_dt.strftime("%A, %Y-%m-%d at %I:%M %p")
    if tz_name:  # guard against a falsy tzname (omit suffix if empty/None)
        trigger_str += " " + tz_name
    success_text = "Success. Triggering on <b>%s</b>.\n\nReminder ID: <code>%s</code>" % \
                   (trigger_str, rem_id)

    # When notif returned a real reminder id, present the confirmation as a
    # one-button MENU carrying an inline "❌ Cancel" action, so the user can
    # cancel the reminder with a tap (equivalent to "/remind cancel. <id>").
    # The menu title is the same success text as the plain confirmation. When
    # there's no valid id (or the menu send fails), fall back to a plain
    # message so the user still gets their confirmation.
    have_valid_id = "id" in rdata
    if have_valid_id:
        m = Menu()
        m.parse_json({
            "title": success_text,
            "options": [
                {
                    "title": "❌ Cancel",
                    "action_key": REMINDER_CANCEL_ACTION_KEY,
                    "action_context": {"reminder_id": rem_id},
                },
            ],
        })
        try:
            service.send_menu(message.chat.id, m, parse_mode="HTML")
            return
        except Exception:
            # fall through to a plain confirmation message below
            pass

    service.send_message(message.chat.id, success_text, parse_mode="HTML")

