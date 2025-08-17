# Imports
import os
import sys
from datetime import datetime
import re
import json

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig
from tasks.finance.base import *
import lib.dtu as dtu
from lib.ynab import YNABConfig, YNAB

class BudgetRegexConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("category_group_title_exclude",   [list],     required=False, default=[])
        ]

    # Takes in a list of YNAB categories and returns the resulting list, using
    # the regexes to filter them.
    def filter_categories(self, categories: list):
        result = []
        for c in categories:
            do_not_include = False

            # compare the group title against regexes
            cgroup_title = c.category_group_name
            for r in self.category_group_title_exclude:
                if re.search(r, cgroup_title):
                    do_not_include = True
                    break

            # if `do_not_include` is set, skip it
            if do_not_include:
                continue

            # include the category in the end result
            result.append(c)

        return result

class TaskJob_Finance_Budget_AutoCategorize_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("ynab",             [YNABConfig],           required=True),
            ConfigField("dialogue_retries", [int],                  required=False, default=5),
            ConfigField("regexes",          [BudgetRegexConfig],    required=False, default=None),
            ConfigField("ynab_flag_color",  [str],                  required=False, default="blue")
        ]

# This taskjob's purpose is to scan through transactions in YNAB and look for
# ones that aren't categorized. If any are found, it should attempt to
# categorize it using... ~AI~.
class TaskJob_Finance_Budget_AutoCategorize(TaskJob_Finance):
    def init(self):
        # this task should run every few hours
        self.refresh_rate = 3600 * 5

        # find the config and parse it
        config_dir = os.path.dirname(os.path.realpath(__file__))
        config_name = os.path.basename(__file__.replace(".py", ".json"))
        config_path = os.path.join(config_dir, config_name)
        self.config = TaskJob_Finance_Budget_AutoCategorize_Config()
        self.config.parse_file(config_path)

    def update(self, todoist, gcal):
        # spin up a YNAB API object
        ynab = YNAB(self.config.ynab)
        
        # get all budgets on this account, and process them individually
        budgets = ynab.get_budgets()
        success = False
        for budget in budgets:
            results = self.process_budget(ynab, budget)
            
            # divide the results into two groups:
            # 1. transactions that had categories assigned to them
            # 2. transactions that did *not* have categories assigned to them
            results_nocat = []
            results_cat = []
            for r in results:
                if r["category"] is None:
                    results_nocat.append(r)
                else:
                    results_cat.append(r)

        return result
    
    # Processes a single budget.
    # Returns `True` if changes were made.
    def process_budget(self, ynab: YNAB, budget):
        # get the last time this taskjob ran
        last_update = self.get_last_update_datetime()

        # get all categories for this budget
        categories = ynab.get_categories(budget.id)

        # get all transactions that are "unapproved" (i.e. haven't been reviewed
        # by a human)
        transactions = ynab.get_transactions_unapproved(budget.id,
                                                        since_date=last_update)
        
        # build a system/intro promopt for the LLM
        prompt_intro = self.create_prompt_intro(ynab, budget, categories)

        # get a session to the speaker
        speaker_session = self.service.get_speaker_session()

        # iterate through each transaction and process it
        results = []
        for t in transactions:
            r = self.process_transaction(
                ynab,
                budget,
                categories,
                t,
                prompt_intro,
                speaker_session
            )

            # if something failed, we will have received `None`. Skip if so
            if r is None:
                self.log("Failed to process transaction: [%s]." % YNAB.transaction_to_str(t))
                continue
            
            # append the result to the output array
            results.append(r)

        # return all results
        return results

    def process_transaction(self,
                            ynab: YNAB,
                            budget,
                            categories,
                            transaction,
                            prompt_intro,
                            speaker_session):
        prompt_main = self.create_prompt_main(ynab, budget, categories, transaction)
        
        # send the prompts to the speaker for processing; do this multiple
        # times to avoid intermittent failures
        result = None
        for i in range(self.config.dialogue_retries):
            try:
                # send the prompts to the dialogue service, and parse the
                # result as a JSON object
                r = self.service.dialogue_oneshot(prompt_intro, prompt_main)
                jdata = json.loads(r)
                rdata = {
                    "transaction": transaction,
                    "category": None
                }

                # make sure the category name was provided
                cname_field = "category_name"
                if cname_field not in jdata:
                    continue

                # if the category name isn't null, try to match it up with a
                # category to assign it to
                if jdata[cname_field] is not None:
                    cname = jdata[cname_field].lower().strip()
                    for c in categories:
                        # if the category names match, set the category in the
                        # result object and return
                        if c.name.lower().strip() == cname:
                            rdata["category"] = c
                            break

                # on success, set the result and break
                result = rdata
                break
            except Exception as e:
                # on failure, continue to the next loop iteration
                continue
        
        # if the above failed, and we didn't get a result, return early
        if result is None:
            return None

        # if a category was chosen, submit the update to YNAB:
        #
        # 1. update the category
        # 2. mark the transaction with a flag (to indicate it was
        #    processed programatically)
        # 3. mark the transaction as approved
        category = result["category"]
        if category is not None:
            ynab.update_transaction(
                budget.id,
                transaction.id,
                transaction_category_id=category.id,
                transaction_flag_color=self.config.ynab_flag_color,
                transaction_approved=True
            )

            # log the update
            self.log("Categorized transaction:          [%s] --> \"%s\"." % (
                YNAB.transaction_to_str(transaction),
                category.name
            ))
        else:
            self.log("Could not categorize transaction: [%s]." %
                     YNAB.transaction_to_str(transaction))

        return result

    # Creates the introductory ("system") prompt for the LLM.
    def create_prompt_intro(self, ynab: YNAB, budget, categories):
        now = datetime.now()

        p = "You are a assistant designed to automatically process transactions in a budget.\n"

        # give the current day and time
        p += "The current date/time is: %s, %s.\n" % (
            dtu.get_weekday_str(now),
            dtu.format_yyyymmdd_hhmmss_12h(now)
        )

        p += "Your job is to process a single transaction, and decide what category it should be assigned to.\n" \
             "Below, you will receive a list of categories to choose from."

        # filter the budget categories with the configured regexes
        fcategories = categories
        if self.config.regexes is not None:
            fcategories = self.config.regexes.filter_categories(categories)

        # go through all categories in this budget and build up a set of JSON
        # objects to describe them.
        fcategories_data = []
        for c in fcategories:
            cdata = {"category_name": c.name}
            if c.note is not None and len(c.note) != 0:
                cdata["category_description"] = c.note

                # get all transactions for this category
                ctrans = ynab.get_transactions_by_category(budget.id, c.id)
                if len(ctrans) > 0:
                    cdata["category_transactions"] = []
                    # build a list of transactions that have unique payees
                    entries = {}
                    for t in ctrans:
                        # grab one of the payee names; doesn't matter which,
                        # but we want to consider all kinds
                        payee_name = self.transaction_get_payee_name(t)
                        if payee_name is not None and payee_name not in entries:
                            entries[payee_name] = t
                            
                            # construct a JSON object to describe the transaction
                            jdata = self.transaction_to_json(t)
                            cdata["category_transactions"].append(jdata)

            # add the dictionary to the main JSON object
            fcategories_data.append(cdata)
        fcategories_str = json.dumps(fcategories_data, indent=4)
        
        # explain the categories
        p += "The JSON object below describes all possible categories. " \
             "Please read the category names and descriptions to determine what category the transaction should belong to. " \
             "Especially consider the descriptions; they may contain special instructions that you must follow. " \
             "Additionally, any existing transactions belonging to these categories are included. " \
             "Use these as additional context to understand where to assign this new transaction.\n"
        p += "```json\n%s\n```\n" % fcategories_str

        # explain output format
        p += "You must produce a single, syntactically-correct JSON object in the following format:\n" \
             "{\n" \
             "    \"category_name\": \"NAME_OF_CATEGORY\"\n" \
             "}\n" \
             "Do not include backtick notation (i.e. \"```json\") in your output.\n" \
             "Replace `NAME_OF_CATEGORY` with the *exact* name of the category you have selected. " \
             "If you do not have enough information to determine the category, you *must* instead set the `category_name` field to `null`. " \
             "You must choose a category name from one of these specific, exact strings:\n"

        # list the category names again
        for c in fcategories:
            p += " - \"%s\"\n" % c.name
        
        p += "Do NOT just guess at what category to pick. Instead, find evidence, such as:\n" \
             "1. A keyword in the transaction matches a keyword in the category name (such as \"groceries\"), or in a category's existing details.\n" \
             "2. A category's description has explicit instructions regarding the transactions, the current date and time, and more, that align with the transaction you are processing.\n" \
             "If there is no clear evidence, do NOT assign it category; mark it as `null`.\n"
        p += "Please do NOT produce any other output besides the JSON object.\n"

        return p
    
    # Creates the main prompt that occurs after the introductory ("system")
    # prompt for the LLM.
    def create_prompt_main(self, ynab: YNAB, budget, categories, transaction):
        jdata = self.transaction_to_json(transaction)
        return json.dumps(jdata, indent=4)

    def transaction_to_json(self, transaction):
        jdata = {
            "transaction_datetime": "%s, %s" % (
                dtu.get_weekday_str(transaction.var_date),
                dtu.format_yyyymmdd(transaction.var_date)
            ),
            "transaction_price": YNAB.get_transaction_amount(transaction),
            "transaction_payee": self.transaction_get_payee_name(transaction),
        }
        if transaction.memo is not None:
            jdata["transaction_description"] = transaction.memo
        if transaction.account_name is not None:
            jdata["transaction_account"] = transaction.account_name
        return jdata

    def transaction_get_payee_name(self, transaction):
        if transaction.payee_name is not None:
            return transaction.payee_name
        if transaction.import_payee_name is not None:
            return transaction.import_payee_name
        if transaction.import_payee_name_original is not None:
            return transaction.import_payee_name_original
        return None

