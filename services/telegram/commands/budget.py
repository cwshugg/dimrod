# Implements the /budget bot command.

# Imports
import os
import sys
import subprocess
import re
import json

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.ynab import YNAB

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
# Builds and returns a string prompt to feed to an LLM for processing the
# context of a transaction message.
def build_transaction_parse_prompt(categories: list, accounts: list, entities: list):
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

    # prepend the price to the string, so the LLM can understand how much the
    # transaction was for
    text = "Transaction price: $%.2f\n\n %s" % (currency_amount, text)
    
    ynab = YNAB(service.config.ynab)
    # ---------- TODO TODO TODO TODO ---------- #
    print(ynab.get_budgets())
    print(ynab.get_budget_by_id("yo"))
    budget_id = "9a09fdac-357d-44b8-a85a-96e87e3cce4d"
    print(ynab.get_budget_by_id(budget_id))
    print("---------- ACCOUNTS ----------\n%s", ynab.get_accounts(budget_id))
    print(ynab.get_account_by_id(budget_id, "108c2c84-11cd-484c-acf0-d85ea8eb75dc"))
    print(ynab.get_account_by_id(budget_id, "yo"))
    print("---------- CATEGORIES ----------\n%s", ynab.get_categories(budget_id))
    print(ynab.get_category_by_id(budget_id, "113f1d24-e325-48db-8f16-4cc2ee318fb5"))
    print(ynab.get_category_by_id(budget_id, "yo"))
    print("---------- ENTITIES ----------\n%s", ynab.get_entities(budget_id))
    print(ynab.get_entity_by_id(budget_id, "b205179e-b316-4d05-8067-854ee93db294"))
    print(ynab.get_entity_by_id(budget_id, "yo"))
    # ---------- TODO TODO TODO TODO ---------- #

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

