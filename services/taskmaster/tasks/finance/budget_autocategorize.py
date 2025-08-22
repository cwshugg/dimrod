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
from lib.ynab import YNABConfig, YNAB, YNABTransactionUpdate

# YNAB imports
from ynab.exceptions import ApiException

class BudgetRegexConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("category_group_title_exclude",   [list],     required=False, default=[]),
            ConfigField("budget_name_exclude",            [list],     required=False, default=[])
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
            result.append(c)

        return result

    def filter_budgets(self, budgets: list):
        result = []
        for b in budgets:
            do_not_include = False

            # compare the budget name against regexes
            for r in self.budget_name_exclude:
                if re.search(r, b.name):
                    do_not_include = True
                    break

            # if `do_not_include` is set, skip it
            if do_not_include:
                continue
            result.append(b)

        return result

class TaskJob_Finance_Budget_AutoCategorize_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("ynab",             [YNABConfig],           required=True),
            ConfigField("dialogue_retries", [int],                  required=False, default=5),
            ConfigField("regexes",          [BudgetRegexConfig],    required=False, default=None)
        ]

# This taskjob's purpose is to scan through transactions in YNAB and look for
# ones that aren't categorized. If any are found, it should attempt to
# categorize it using... ~AI~.
class TaskJob_Finance_Budget_AutoCategorize(TaskJob_Finance):
    def init(self):
        # this task should run every few hours
        self.refresh_rate_default = 3600 * 5
        self.refresh_rate = self.refresh_rate_default

        # however, YNAB imposes rate limits that might affect us. This refresh
        # rate will be used if we encounter rate limiting errors.
        #
        # see YNAB's API rate limit documentation here:
        # https://api.ynab.com/#rate-limiting
        #
        # YNAB seems to allow up to 200 requests per hour. Once that limit is
        # hit, the "sliding window" of an hour is used to only allow new
        # requests once the total number of requests in the past hour is below
        # a threshold.
        #
        # so, if we are getting rate limited by YNAB, we'll use this refresh
        # rate to try again in an hour and a half
        self.refresh_rate_api_rate_limit = int(3600 * 1.5)

        # find the config and parse it
        config_dir = os.path.dirname(os.path.realpath(__file__))
        config_name = os.path.basename(__file__.replace(".py", ".json"))
        config_path = os.path.join(config_dir, config_name)
        self.config = TaskJob_Finance_Budget_AutoCategorize_Config()
        self.config.parse_file(config_path)

    def update(self, todoist, gcal):
        # update the refresh rate to the the default value. It's possible the
        # last invocation of this taskjob ended in a YNAB API rate limit, which
        # caused us to shorten the refresh rate. This ensures that it is
        # returned to its normal value
        self.refresh_rate = self.refresh_rate_default
        
        # spin up a YNAB API object
        ynab = YNAB(self.config.ynab)
        
        try:
            # get all budgets on this account, and process them individually
            budgets = self.config.regexes.filter_budgets(ynab.get_budgets())
            success = False
            for budget in budgets:
                # if the budget name matches one of the exclude regexes, skip it
                # entirely
    
                updates = self.process_budget(ynab, budget)
                updates_len = len(updates)
                updates_fresh_len = len([u.has_updates() for u in updates])
                success = success or len(updates) > 0
                
                # if there were no new updates, but there were transactions to
                # process, put together a message saying that nothing could be
                # auto-processed
                if updates_len > 0 and updates_fresh_len == 0:
                    report = "There are %d pending transactions. " \
                             "None of them could be auto-processed. " \
                             "Please manually update them through the YNAB app."
                    report_title = "Pending Transactions in \"%s\"" % budget.name
                    self.service.msghub.post(report, title=report_title)
                # if there was at least one new update, put together a report
                elif updates_fresh_len > 0:
                    report = self.create_msghub_report(updates)
                    report_title = "Autocategorization of \"%s\"" % budget.name
                    self.service.msghub.post(report, title=report_title)

        # catch any YNAB API exceptions that occur
        except ApiException as e:
            # if we're being rate limited by YNAB...
            if e.status == 429:
                self.log("Received rate-limiting response from YNAB API "
                         "(%s - \"%s\"). "
                         "Dropping down to refresh rate of %d seconds." % (
                    e.status,
                    e.reason,
                    self.refresh_rate_api_rate_limit
                ))

                # use the rate-limit-specific refresh rate, which will allow
                # this taskjob to be run sooner than normal, around the time
                # after which all rate limiting issues should be gone
                self.refresh_rate = self.refresh_rate_api_rate_limit

                # return `True` to indicate that the taskjob succeeded (even
                # though it failed due to rate limiting). This will force the
                # taskmaster system to hold off on invoking it again until its
                # next refresh rate (which we just updated)
                return True

            # if some other exception occurred, we want to raise it
            raise e

        # TODO - send notification for transactions that *were* categorized
        # TODO - send Telegram poll/question to manually categorize those that were *not* categorized

        return success
    
    # Processes a single budget.
    # Returns `True` if changes were made.
    def process_budget(self, ynab: YNAB, budget):
        # get the last time this taskjob ran
        last_update = self.get_last_update_datetime()

        # get all categories for this budget
        categories = ynab.get_categories(budget.id)

        # get all transactions that are "unapproved" (i.e. haven't been reviewed
        # by a human)
        transactions = ynab.get_transactions_unapproved_uncategorized(
            budget.id,
            since_date=last_update
        )
        transactions_len = len(transactions)

        # log the number of transactions retreived
        if transactions_len > 0:
            self.log("Retrieved %d new transactions for budget \"%s\"." % (
                transactions_len,
                budget.name
            ))
        
        # build a system/intro promopt for the LLM
        prompt_intro = self.create_prompt_intro(ynab, budget, categories)
        print("PROMPT:\n%s" % prompt_intro) # TODO REMOVE

        # get a session to the speaker
        speaker_session = self.service.get_speaker_session()

        # iterate through each transaction and process it
        updates = []
        for t in transactions:
            u = self.create_transaction_update(
                ynab,
                budget,
                categories,
                t,
                prompt_intro,
                speaker_session
            )

            # if something failed, we will have received `None`. Skip if so
            if u is None:
                continue
            
            # append the result to the output array
            updates.append(u)
        
        # if updates were created, submit them now
        updates_len = len(updates)
        if updates_len > 0:
            r = ynab.update_transactions(
                budget.id,
                updates
            )

            # TODO - do something with response (?)
            
            # log the updates
            self.log("Successfully updated %d transactions." % updates_len)

        # return all update objects
        return updates
    
    # Generates a `YNABTransactionUpdate` object given, which will be used
    # later to update each YNAB transaction remotely.
    def create_transaction_update(self,
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

                # create an update object
                update = YNABTransactionUpdate()
                update.init_defaults()
                update.parse_json({"id": transaction.get_id()})
                update.transaction = transaction

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
                            update.update_category_id = c.id
                            update.update_approved = True
                            update.category = c
                            break

                # on success, set the result and break
                result = update
                break
            except Exception as e:
                # on failure, continue to the next loop iteration
                continue
        
        # if the above failed, and we didn't get a result, return early
        if result is None or not result.has_updates():
            self.log("No new updates for transaction:     [%s]" % transaction)
            return None 
        self.log("Determined updates for transaction: [%s] --> %s" % (
            transaction,
            result
        ))

        return result

    # Creates the introductory ("system") prompt for the LLM.
    def create_prompt_intro(self, ynab: YNAB, budget, categories):
        now = datetime.now()

        p = "You are an assistant designed to automatically process transactions in a budget.\n"

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

        # explain output format
        p += "You must produce a single, syntactically-correct JSON object in the following format:\n" \
             "{\n" \
             "    \"category_name\": \"NAME_OF_CATEGORY\"\n" \
             "}\n" \
             "Do not include backtick notation (i.e. \"```json\") in your output.\n" \
             "Replace `NAME_OF_CATEGORY` with the *exact* name of the category you have selected. " \
             "If you do not have enough information to determine the category, you *must* instead set the `category_name` field to `null`. " \
             "You must choose a category name from one of these specific, exact strings:\n"

        # list the category names and descriptions
        for c in fcategories:
            line = " - \"%s\"" % c.name
            if c.note is not None and len(c.note) > 0:
                line += " - %s" % c.note
            p += "%s\n" % line
        
        p += "Do NOT just guess at what category to pick. " \
             "Instead, use the category descriptions as guidance for what to choose. " \
             "If there is no clear evidence, do NOT assign it category; mark it as `null`.\n"
        p += "Please do NOT produce any other output besides the JSON object.\n"

        return p
    
    # Creates the main prompt that occurs after the introductory ("system")
    # prompt for the LLM.
    def create_prompt_main(self, ynab: YNAB, budget, categories, transaction):
        jdata = self.transaction_to_prompt_json(transaction)
        return json.dumps(jdata, indent=4)

    def transaction_to_prompt_json(self, transaction):
        jdata = {
            "transaction_datetime": "%s, %s" % (
                dtu.get_weekday_str(transaction.get_date()),
                dtu.format_yyyymmdd(transaction.get_date())
            ),
            "transaction_price": transaction.get_amount(),
            "transaction_payee": transaction.get_payee_name()
        }
        if transaction.get_description() is not None:
            jdata["transaction_description"] = transaction.get_description()
        if transaction.get_account_name() is not None:
            jdata["transaction_account"] = transaction.get_account_name()
        return jdata

    def create_msghub_report(self, updates: list):
        # iterate through all updates and build a message that we'll
        # send out via the taskmaster's msghub
        new_category = []
        no_change = []
        for u in updates:
            t = u.transaction
            
            # list all transactions that got NO updates and thus need
            # to be manually sorted in the YNAB app
            if not u.has_updates():
                no_change.append("• <s - %s$%.2f at \"%s\"" % (
                    dtu.format_yyyymmdd(t.get_date()),
                    "-" if t.get_amount() < 0 else "",
                    abs(t.get_amount()),
                    t.get_payee_name()
                ))
            # list all transactions that were given a new category
            elif u.update_category_id is not None:
                new_category.append("• <s - %s$%.2f at \"%s\" - \"%s\"" % (
                    dtu.format_yyyymmdd(t.get_date()),
                    "-" if t.get_amount() < 0 else "",
                    abs(t.get_amount()),
                    t.get_payee_name(),
                    u.category.name
                ))

        new_category_len = len(new_category)
        no_change_len = len(no_change)

        msg = ""
        if new_category_len > 0:
            msg += "Categorized %d transactions.\n\n" % new_category_len
            msg += "%s\n\n" % "\n".join(new_category)
        if no_change_len > 0:
            msg += "Couldn't update %d transactions.\n\n" % no_change_len
            msg += "%s\n\n" % "\n".join(no_change)
            msg += "Please manually update the transactions that could not be updated automatically.\n"
        
        return msg

