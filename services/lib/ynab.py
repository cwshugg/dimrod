# This module provides a basic wrapper around the official YNAB (You Need A
# Budget) Python SDK.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# YNAB imports
import ynab

# Local imports
from lib.config import Config, ConfigField

# An object representing configured inputs for a GoogleCalendar object.
class YNABConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("access_token", [str], required=True),
        ]

class YNAB:
    # Constructor. Takes in a Todoist API key.
    def __init__(self, config: YNABConfig):
        self.config = config
        self.client = None

    # Initializes the class' API instance (if it's not yet initialized). The
    # API object is returned.
    def api(self):
        if self.client is None:
            # create a configuration object, then use it to create an API
            # Client object.
            config = ynab.Configuration(
                access_token = self.config.access_token
            )
            self.client = ynab.ApiClient(config)

        # return the client object
        return self.client
    
    # Returns a YNAB budget-specific API object.
    def api_budgets(self):
        return ynab.BudgetsApi(self.api())
    
    # Returns a YNAB accounts-specific API object.
    def api_accounts(self):
        return ynab.AccountsApi(self.api())
    
    # Returns a YNAB category-specific API object.
    def api_categories(self):
        return ynab.CategoriesApi(self.api())
    
    # Returns a YNAB entity-specific API object. (YNAB refers to these as
    # "payees")
    def api_entities(self):
        return ynab.PayeesApi(self.api())
    
    # Returns all YNAB budgets.
    def get_budgets(self):
        api = self.api_budgets()
        r = api.get_budgets(include_accounts=True)
        rdata = r.data
        return rdata.budgets
    
    # Returns a budget object based on its ID.
    # Returns `None` if the budget ID does not exist.
    def get_budget_by_id(self, budget_id: str):
        api = self.api_budgets()
        try:
            r = api.get_budget_by_id(budget_id)
            return r.data.budget
        except:
            return None
    
    # Using the provided budget ID, returns a list of accounts synced with the
    # budget. Any accounts that are marked as deleted are not returned.
    def get_accounts(self, budget_id: str):
        api = self.api_accounts()
        r = api.get_accounts(budget_id)
        accounts = r.data.accounts

        result = []
        for acc in accounts:
            if acc.deleted:
                continue
            result.append(acc)
        return result

    # Returns an account object based on its ID.
    # Returns `None` if the account ID doesn't exist.
    def get_account_by_id(self, budget_id: str, account_id: str):
        api = self.api_accounts()
        try:
            r = api.get_account_by_id(budget_id, account_id)
            return r.data.account
        except:
            return None
    
    # Returns a master list of *all* accounts under *all* budgets.
    # The list of budgets can be specified. If they are not, they will be
    # retrieved from the YNAB API during this function.
    def get_accounts_all_budgets(self, budgets=None):
        if budgets is None:
            budgets = self.get_budgets()

        result = []
        for budget in budgets:
            result += self.get_accounts(budget.id)
        return result
    
    # Returns a list of a budget's categories.
    # Any category that is marked as deleted is not included.
    def get_categories(self, budget_id: str):
        api = self.api_categories()
        r = api.get_categories(budget_id)

        # iterate through each category group and combine the category lists
        # into one master list
        result = []
        for group in r.data.category_groups:
            for cat in group.categories:
                if cat.deleted:
                    continue
                result.append(cat)
        return result
    
    # Returns a category object based on its ID.
    # Returns `None` if the account ID doesn't exist.
    def get_category_by_id(self, budget_id: str, category_id: str):
        api = self.api_categories()
        try:
            r = api.get_category_by_id(budget_id, category_id)
            return r.data.category
        except:
            return None
    
    # Returns a list of all entities (payees) belonging to a budget.
    # Entities that are marked as deleted are not included.
    def get_entities(self, budget_id: str):
        api = self.api_entities()
        r = api.get_payees(budget_id)
        payees = r.data.payees

        result = []
        for p in payees:
            if p.deleted:
                continue
            result.append(p)

        return result
    
    # Returns an entity (payee) based on its ID.
    # Returns `None` if the ID does not exist.
    def get_entity_by_id(self, budget_id: str, entity_id: str):
        api = self.api_entities()
        try:
            r = api.get_payee_by_id(budget_id, entity_id)
            return r.data.payee
        except:
            return None

