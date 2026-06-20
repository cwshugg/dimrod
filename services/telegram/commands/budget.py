# Implements the /budget bot command.

# Imports
import os
import sys
import subprocess
import re
import json
import html
import calendar
from datetime import datetime, date

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.ynab import YNAB
from lib.oracle import OracleSession

transaction_context_parse_intro = "" \
    "You are an assistant that examines human-written budget transaction entries.\n" \
    "These messages are intended to log transactions that were made by the human.\n" \
    "\n" \
    "Please examine the following message and determine the following information:\n" \
    "\n" \
    "1. The entity, vendor, store, person, or location at which the transaction was made. " \
    "(Example: Wegmans - the grocery store, McDonalds - the restaurant, Dan - the human's friend, or even a paper check.)\n" \
    "2. The category under which this transaction should be logged. " \
    "(You will receive a list of categories to choose from later in this message.)\n" \
    "3. The name of the account, credit card, or medium through which the payment was made. " \
    "(You will receive a list of accounts names to choose from later in this message.)\n" \
    "4. Any other relevant information or context, represented as a short memo for the transaction.\n" \
    "\n" \
    "Please respond with a syntactically-correct JSON object with this information, following this exact format:\n" \
    "\n" \
    "{\n" \
    "    \"entity\": \"Wegmans\",\n" \
    "    \"category\": \"Groceries and Supplies\",\n" \
    "    \"account\": \"Capital One Venture Credit Card\",\n" \
    "    \"memo\": \"Buying supplies to bake a cake.\",\n" \
    "}\n" \
    "\n" \
    "If you believe some of this information was not specified, please leave the relevant fields as `null`.\n" \
    "For the category and account fields, please follow the exact wording of the option you chose from the lists provided below.\n" \
    "\n" \
    "When choosing a category for the transaction, please choose from this list:\n" \
    "%s\n" \
    "\n" \
    "When choosing an account for the transaction, please choose from this list:\n" \
    "%s\n" \
    "\n" \
    "When choosing an entity for the transaction, please try to choose from this list:\n" \
    "%s\n" \
    "If you cannot find a matching entity, please come up with your own based on the human's message.\n" \
    "If the human did not provide enough information, please leave the entity as null.\n" \
    "\n" \
    "Only respond with the JSON object shown above.\n" \
    "Do not respond with anything else.\n" \
    "If there is not enough information to fill the JSON fields, please return a JSON object with all fields set to `null`.\n" \
    "Please also attempt to correct typos.\n" \
    "If a word typed by the human is similar to the name of a store, category, or account name, please try your best to infer what the human meant.\n" \
    "Furthermore, you will see the transaction price in the message.\n" \
    "Use the transaction price to further deduce what category the transaction should be placed in.\n" \
    "If the price is POSITVE, this means the human has SPENT money.\n" \
    "If the price is NEGATIVE, this means the human has GAINED money.\n" \
    "Thank you!"


# ================================= Helpers ================================== #
def build_transaction_parse_prompt(categories: list, accounts: list, entities: list):
    """Builds and returns a string prompt to feed to an LLM for processing the
    context of a transaction message.
    """
    # build a string listing all categories
    category_str = ""
    for c in categories:
        category_str += "- %s\n" % c

    # build a string listing all account names
    account_str = ""
    for a in accounts:
        account_str += "- %s\n" % c

    # build a string listing all entity (payee) names
    entity_str = ""
    for e in entities:
        entity_str += "- %s\n" % e

    p = transaction_context_parse_intro % (category_str, account_str, entity_str)
    return p

def parse_currency(text: str):
    """Attempts to parse a currency amount from the given string.

    The amount is returned, or `None` is returned if parsing failed.
    """
    text = text.strip().lower()

    # check to ensure the string begins with a currency symbol. It can be
    # preceded by, or followed by, a negative sign
    currency_symbols = ["$"]
    value_multiplier = 0
    for symbol in currency_symbols:
        symbol_with_leading_minus = "-" + symbol
        symbol_with_trailing_minus = symbol + "-"
        
        # depending on what the text starts with, update the
        # `value_multiplier`. This will help us later with determining if the
        # final value is positive or negative
        #
        # while we're at it, chop off the beginning of the string, now that
        # we've captured this information
        if text.startswith(symbol_with_trailing_minus):
            value_multiplier = -1
            text = text[len(symbol_with_trailing_minus):]
            break
        elif text.startswith(symbol):
            value_multiplier = 1
            text = text[len(symbol):]
            break
        elif text.startswith(symbol_with_leading_minus):
            value_multiplier = -1
            text = text[len(symbol_with_leading_minus):]
            break

    # if the above loop failed, then no currency symbol was found; return early
    if value_multiplier == 0:
        return None

    # attepmt to convert the rest of the string into a float, and multiply it
    # by the value multiplier. If converting to a float fails, return None
    try:
        return float(text) * float(value_multiplier)
    except:
        return None


def _esc(text: str) -> str:
    """Escape text for safe use in Telegram HTML messages."""
    if text is None:
        return ""
    return html.escape(str(text))


# =================================== Main =================================== #
def _get_treasurer_session(service, message):
    """Create and authenticate an OracleSession with the Treasurer service.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    if not hasattr(service.config, 'treasurer') or \
            service.config.treasurer is None:
        service.send_message(message.chat.id,
                             "Treasurer is not configured for this bot.")
        return None
    session = OracleSession(service.config.treasurer)
    try:
        r = session.login()
    except Exception:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Treasurer. "
                             "It might be offline.")
        return None

    if r.status_code != 200 or not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Treasurer.")
        return None

    return session


def _budget_list(service, message, args):
    """Handle '/budget list' — lists configured budgets."""
    session = _get_treasurer_session(service, message)
    if session is None:
        return

    r = session.get("/budgets")
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Failed to retrieve budgets. (%s)" %
                             session.get_response_message(r))
        return

    data = session.get_response_json(r)
    budgets = data.get("budgets", []) if isinstance(data, dict) else data
    if not budgets or len(budgets) == 0:
        service.send_message(message.chat.id, "No budgets configured.")
        return

    msg = "<b>Configured Budgets:</b>\n\n"
    for b in budgets:
        name = b.get("name", b.get("budget_name", "Unknown"))
        bid = b.get("id", b.get("budget_id", ""))
        msg += "• <code>%s</code> — <code>%s</code>\n" % (
            _esc(name), _esc(bid)
        )

    service.send_message(message.chat.id, msg, parse_mode="HTML")


def _budget_summary(service, message, args):
    """Handle '/budget summary <budget_name_or_id> [start_date] [end_date]'.

    If no dates provided, uses current month.
    If one date provided, uses that month (first to last day).
    If two dates provided, uses that range.
    """
    if len(args) < 3:
        service.send_message(message.chat.id,
                             "Usage: <code>/budget summary "
                             "&lt;budget_name_or_id&gt; "
                             "[YYYY-MM-DD] [YYYY-MM-DD]</code>",
                             parse_mode="HTML")
        return

    session = _get_treasurer_session(service, message)
    if session is None:
        return

    # Parse from the end: dates are YYYY-MM-DD formatted args at the tail.
    # Everything between "summary" and the dates is the budget identifier.
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    trailing_dates = []
    remaining_args = args[2:]  # everything after "/budget summary"

    # Check last two args for dates (scan from the end)
    while len(remaining_args) > 0 and len(trailing_dates) < 2:
        if date_pattern.match(remaining_args[-1]):
            trailing_dates.insert(0, remaining_args.pop())
        else:
            break

    # Whatever is left is the budget identifier
    budget_identifier = " ".join(remaining_args).strip()
    if not budget_identifier:
        service.send_message(message.chat.id,
                             "Please specify a budget name or ID.")
        return

    # Determine the date range
    today = date.today()
    if len(trailing_dates) == 2:
        start_date = trailing_dates[0]
        end_date = trailing_dates[1]
    elif len(trailing_dates) == 1:
        try:
            dt = datetime.strptime(trailing_dates[0], "%Y-%m-%d")
            start_date = "%d-%02d-01" % (dt.year, dt.month)
            last_day = calendar.monthrange(dt.year, dt.month)[1]
            end_date = "%d-%02d-%02d" % (dt.year, dt.month, last_day)
        except ValueError:
            service.send_message(message.chat.id,
                                 "Invalid date format. Use YYYY-MM-DD.",
                                 parse_mode="HTML")
            return
    else:
        # No dates — current month
        start_date = today.strftime("%Y-%m-01")
        last_day = calendar.monthrange(today.year, today.month)[1]
        end_date = "%d-%02d-%02d" % (today.year, today.month, last_day)

    # Try by budget_name first, then by budget_id
    payload = {
        "budget_name": budget_identifier,
        "start_date": start_date,
        "end_date": end_date,
    }
    r = session.get("/summary", payload=payload)

    # If not found by name, try by ID
    if r.status_code == 404:
        payload = {
            "budget_id": budget_identifier,
            "start_date": start_date,
            "end_date": end_date,
        }
        r = session.get("/summary", payload=payload)

    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Failed to generate summary. (%s)" %
                             session.get_response_message(r))
        return

    summary = session.get_response_json(r)
    msg = _format_summary_message(summary)
    service.send_message(message.chat.id, msg, parse_mode="HTML")


def _format_summary_message(summary: dict) -> str:
    """Formats a summary dict into an HTML message for Telegram."""
    budget_name = summary.get("budget_name", "Unknown")
    start_date = summary.get("start_date", "?")
    end_date = summary.get("end_date", "?")
    total_income = summary.get("total_income", 0.0)
    total_expenses = summary.get("total_expenses", 0.0)
    net = summary.get("net", 0.0)
    transaction_count = summary.get("transaction_count", 0)

    net_sign = "+" if net >= 0 else "-"
    net_abs = abs(net)

    msg = "<b>%s</b>\n" % _esc(budget_name)
    msg += "%s → %s\n\n" % (start_date, end_date)
    msg += "• Income: <code>$%s</code>\n" % format(total_income, ",.2f")
    msg += "• Expenses: <code>$%s</code>\n" % format(total_expenses, ",.2f")
    msg += "• Net: <code>%s$%s</code>\n" % (net_sign, format(net_abs, ",.2f"))
    msg += "• Transactions: %d\n" % transaction_count

    # Top expense categories
    categories = summary.get("categories", {})
    if categories:
        cat_list = [(name, data["amount"], data.get("group_name"))
                    for name, data in categories.items()]
        cat_list.sort(key=lambda x: abs(x[1]), reverse=True)
        msg += "\n<b>Top Expense Categories:</b>\n\n"
        for name, amount, _ in cat_list[:10]:
            msg += "• %s: <code>$%s</code>\n" % (
                _esc(name), format(abs(amount), ",.2f")
            )

    # Top income transactions
    income_txns = summary.get("income_transactions", [])
    if income_txns:
        msg += "\n<b>Top Income:</b>\n\n"
        for txn in income_txns[:10]:
            memo = txn.get("memo")
            if memo:
                msg += "• %s: <code>$%s</code> (%s) — %s\n" % (
                    _esc(txn["payee_name"]),
                    format(txn["amount"], ",.2f"),
                    txn["date"],
                    _esc(memo)
                )
            else:
                msg += "• %s: <code>$%s</code> (%s)\n" % (
                    _esc(txn["payee_name"]),
                    format(txn["amount"], ",.2f"),
                    txn["date"]
                )

    return msg


def command_budget(service, message, args: list):
    """Main function for the /budget command."""
    # Check for subcommands
    if len(args) > 1:
        subcommand = args[1].strip().lower()

        if subcommand == "list":
            return _budget_list(service, message, args)
        if subcommand == "summary":
            return _budget_summary(service, message, args)
        if subcommand == "help":
            _budget_help(service, message)
            return

    # Fall through to the original transaction-logging behavior
    # look for an argument that represents some amount of currency
    currency_amount = None
    currency_index = None
    for (i, arg) in enumerate(args):
        currency_amount = parse_currency(arg)
        if currency_amount is not None:
            currency_index = i
            break

    # if currency wasn't found, show help
    if currency_amount is None:
        _budget_help(service, message)
        return

    # join all other arguments together, excluding the very first argument
    # (which contains the slash command), and the argument from which we parsed
    # the currency amount
    text = " ".join(args[i] for i in range(len(args)) if i not in [0, currency_index])

    # prepend the price to the string, so the LLM can understand how much the
    # transaction was for
    text = "Transaction price: $%.2f\n\n %s" % (currency_amount, text)
    
    ynab = YNAB(service.config.ynab)

    # determine a list of categories for the LLM to choose from
    categories = [
        "Groceries",
        "Eating Out",
        "Miscellaneous",
    ] # TODO TODO TODO TODO TODO PULL FROM YNAB API
    
    # retrieve all YNAB budgets, and pull down lists of all accounts,
    # categories, and entities/payees. We'll give this information to the LLM
    # to sort through
    budgets = ynab.get_budgets()
    accounts = ynab.get_accounts_all_budgets(budgets=budgets)
    # TODO - ynab.get_categories_all_budgets()
    # TODO - ynab.get_entities_all_budgets()
    print("========================= ALL ACCOUNTS\n%s" % accounts)

    # determine a list of entities for the LLM to choose from
    entities = [
        "Wegmans",
        "Shell",
        "Reagan",
    ] # TODO TODO TODO TODO TODO PULL FROM YNAB API
    
    # send a prompt to the LLM to retrieve a JSON object containing the
    # transaction context. Try this a few times, in case the LLM doesn't
    # produce syntactically-correct JSON 
    tries = 8
    intro = build_transaction_parse_prompt(categories, accounts, entities)
    jdata = None
    for t in range(tries):
        jdata_str = service.dialogue_oneshot(intro, text)
        try:
            jdata = json.loads(jdata_str)
            break
        except Exception as e:
            service.log.write("Failed to communicate with LLM: %s" % e)
            continue

    # if all tries failed, return early and send a message indicating that
    # something didn't work right in the LLM
    if jdata is None:
        msg = "Sorry, something went wrong with communicating with the LLM."
        service.send_message(message.chat.id, msg)
        return

    # add the price to the JSON object:
    jdata["price"] = currency_amount

    # ---------- TODO TODO DEBUGGING ---------- #
    service.send_message(message.chat.id, json.dumps(jdata, indent=4))
    # ---------- TODO TODO DEBUGGING ---------- #

    return


def _budget_help(service, message):
    """Show help text for the /budget command."""
    msg = "<b>Budget Commands:</b>\n\n"
    msg += "  <code>/budget list</code>"
    msg += " — List configured budgets\n"
    msg += "  <code>/budget summary &lt;name_or_id&gt;</code>"
    msg += " — Summary for current month\n"
    msg += "  <code>/budget summary &lt;name_or_id&gt; YYYY-MM-DD</code>"
    msg += " — Summary for that month\n"
    msg += "  <code>/budget summary &lt;name_or_id&gt; YYYY-MM-DD YYYY-MM-DD</code>"
    msg += " — Summary for date range\n"
    msg += "  <code>/budget &lt;$amount&gt; [description]</code>"
    msg += " — Log a transaction\n"
    msg += "  <code>/budget help</code>"
    msg += " — Show this help message\n"
    msg += "\n<b>Examples:</b>\n"
    msg += "  <code>/budget list</code>\n"
    msg += "  <code>/budget summary Master Budget</code>\n"
    msg += "  <code>/budget summary Master Budget 2026-06-01 2026-06-30</code>\n"
    msg += "  <code>/budget $12.50 Wegmans groceries</code>\n"
    service.send_message(message.chat.id, msg, parse_mode="HTML")

