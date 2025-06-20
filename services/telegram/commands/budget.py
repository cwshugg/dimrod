# Implements the /budget bot command.

# Imports
import os
import sys
import subprocess
import re

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

transaction_context_parse_intro = "" \
    "You are an assistant that examines human-written budget transaction entries.\n" \
    "These messages are intended to log transactions that were made by the human.\n" \
    "\n" \
    "Please examine the following message and determine the following information:\n" \
    "\n" \
    "1. The vendor, store, or location at which the transaction was made. " \
    "(Example: Wegmans - the grocery store, McDonalds - the restaurant, or even a paper check.)\n" \
    "2. The category under which this transaction should be logged. " \
    "(You will receive a list of categories to choose from later in this message.)\n" \
    "3. The name of the account, credit card, or medium through which the payment was made. " \
    "(You will receive a list of accounts names to choose from later in this message.)\n" \
    "4. Any other relevant information or context, represented as a short memo for the transaction.\n" \
    "\n" \
    "Please respond with a syntactically-correct JSON object with this information, following this exact format:\n" \
    "\n" \
    "{\n" \
    "    \"vendor\": \"Wegmans\",\n" \
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
    "Only respond with the JSON object shown above.\n" \
    "Do not respond with anything else.\n" \
    "If there is not enough information to fill the JSON fields, please return a JSON object with all fields set to `null`.\n" \
    "Please also attempt to correct typos.\n" \
    "If a word typed by the human is similar to the name of a store, category, or account name, please try your best to infer what the human meant.\n" \
    "Thank you!"


# ================================= Helpers ================================== #
# Builds and returns a string prompt to feed to an LLM for processing the
# context of a transaction message.
def build_transaction_parse_prompt(categories: list, accounts: list):
    # build a string listing all categories
    category_str = ""
    for c in categories:
        category_str += "- %s\n" % c

    # build a string listing all account names
    account_str = ""
    for a in accounts:
        account_str += "- %s\n" % c

    p = transaction_context_parse_intro % (category_str, account_str)
    return p

# Attempts to parse a currency amount from the given string.
# The amount is returned, or `None` is returned if parsing failed.
def parse_currency(text: str):
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


# =================================== Main =================================== #
# Main function.
def command_budget(service, message, args: list):
    # look for an argument that represents some amount of currency
    currency_amount = None
    currency_index = None
    for (i, arg) in enumerate(args):
        currency_amount = parse_currency(arg)
        if currency_amount is not None:
            currency_index = i
            break

    # if currency wasn't found, complain and exit
    if currency_amount is None:
        msg = "In order to log something in the budget, " \
              "you must specify a dollar amount.\n" \
              "\n" \
              "For example:\n" \
              "• $5\n" \
              "• $12.50\n" \
              "• -$100.\n" \
              "\n" \
              "Positive values represent money spent.\n" \
              "Negative values represent money gained."
        service.send_message(message.chat.id, msg)
        return

    # join all other arguments together, excluding the very first argument
    # (which contains the slash command), and the argument from which we parsed
    # the currency amount
    text = " ".join(args[i] for i in range(len(args)) if i not in [0, currency_index])
    
    # determine a list of categories for the LLM to choose from
    categories = [
        "Groceries",
        "Eating Out",
        "Miscellaneous",
    ] # TODO TODO TODO TODO TODO PULL FROM YNAB API
    
    # determine a list of accounts for the LLM to choose from
    accounts = [
        "Checking Account",
        "Capital One CC",
        "Chase Bank CC",
    ] # TODO TODO TODO TODO TODO PULL FROM YNAB API
    
    # send a prompt to the LLM to retrieve a JSON object containing the
    # transaction context
    intro = build_transaction_parse_prompt(categories, accounts)
    context_jdata_str = service.dialogue_oneshot(intro, text)

    # ---------- TODO TODO DEBUGGING ---------- #
    msg = "Transaction amount: %f\n" \
          "\n" \
          "%s" \
          % (currency_amount, context_jdata_str)
    service.send_message(message.chat.id, msg)
    # ---------- TODO TODO DEBUGGING ---------- #

    return

