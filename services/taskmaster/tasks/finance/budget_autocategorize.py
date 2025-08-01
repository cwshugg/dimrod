# Imports
import os
import sys
from datetime import datetime

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

class TaskJob_Finance_Budget_AutoCategorize_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("ynab",         [YNABConfig],     required=True),
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
        result = False
        for budget in budgets:
            result = result or self.process_budget(ynab, budget)

        return result
    
    # Processes a single budget.
    # Returns `True` if changes were made.
    def process_budget(self, ynab: YNAB, budget):
        # get the last time this taskjob ran
        last_update = self.get_last_update_datetime()

        # get all categories for this budget
        categories = ynab.get_categories(budget.id)

        # get all transactions that are "unapproved" (i.e. haven't been reviewed
        # off by a human)
        transactions = ynab.get_transactions_unapproved(budget.id,
                                                        since_date=last_update)

        # iterate through each transaction and process it
        for t in transactions:
            self.process_transaction(ynab, t)

    def process_transaction(self,
                            ynab: YNAB,
                            transaction,
                            categories):
        # TODO
        pass

