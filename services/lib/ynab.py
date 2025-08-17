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
from ynab.models.transaction_cleared_status import TransactionClearedStatus
from ynab.models.transaction_flag_color import TransactionFlagColor
from ynab.models.existing_transaction import ExistingTransaction
from ynab.models.put_transaction_wrapper import PutTransactionWrapper

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

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
    
    # Returns a YNAB transaction-specific API object.
    def api_transactions(self):
        return ynab.TransactionsApi(self.api())
    
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
    
    # Returns all transactions occurring after the `since_date` field for a
    # budget. If `since_date` is `None`, then *all* transactions are retrieved.
    def get_transactions(self,
                         budget_id: str,
                         since_date: datetime = None,
                         transaction_type: str = None):
        api = self.api_transactions()
    
        # format the `since_date` in YYYY-MM-DD format
        since_date_str = None
        if since_date is not None:
            since_date_str = dtu.format_yyyymmdd(since_date)
        
        # poll the API and return the list of transactions
        r = api.get_transactions(budget_id,
                                 since_date=since_date_str,
                                 type=transaction_type)
        transactions = r.data.transactions

        # iterate through the transactions and save only the ones that aren't
        # marked as deleted
        result = []
        for t in transactions:
            if t.deleted:
                continue
            result.append(t)

        return result

    # Returns all unapproved transactions for a budget.
    def get_transactions_unapproved(self,
                                    budget_id: str,
                                    since_date: datetime = None):
        return self.get_transactions(budget_id,
                                     since_date=since_date,
                                     transaction_type="unapproved")

    # Returns all uncategorized transactions for a budget.
    def get_transactions_uncategorized(self,
                                       budget_id: str,
                                       since_date: datetime = None):
        return self.get_transactions(budget_id,
                                     since_date=since_date,
                                     transaction_type="uncategorized")
    
    # Retrieves all transactions belonging to a specific category.
    def get_transactions_by_category(self,
                                     budget_id: str,
                                     category_id: str,
                                     since_date: datetime = None,
                                     transaction_type: str = None):
        api = self.api_transactions()
    
        # format the `since_date` in YYYY-MM-DD format
        since_date_str = None
        if since_date is not None:
            since_date_str = dtu.format_yyyymmdd(since_date)
        
        # poll the API and return the list of transactions
        r = api.get_transactions_by_category(budget_id,
                                             category_id,
                                             since_date=since_date_str,
                                             type=transaction_type)
        transactions = r.data.transactions

        # iterate through the transactions and save only the ones that aren't
        # marked as deleted
        result = []
        for t in transactions:
            if t.deleted:
                continue
            result.append(t)

        return result
    
    # Returns the converted transaction amount.
    @staticmethod
    def get_transaction_amount(transaction):
        return float(transaction.amount) / 1000.0
    
    @staticmethod
    def transaction_to_str(transaction):
        r = "Date=\"%s\" " % dtu.format_yyyymmdd(transaction.var_date)
        r += "Amount=\"%.2f\" " % YNAB.get_transaction_amount(transaction)
        r += "Entity=\"%s\" " % transaction.payee_name
        if transaction.memo is not None:
            r += "Description=\"%s\"" % transaction.memo
        return r
    
    # Updates a transaction with the provided transaction ID. One or more of
    # the optional fields must be specified in order for the update to be sent
    # to YNAB.
    def update_transaction(self,
                           budget_id: str,
                           transaction_id: str,
                           transaction_account_id: str = None,
                           transaction_date: datetime = None,
                           transaction_amount: float = None,
                           transaction_entity_id: str = None,
                           transaction_category_id: str = None,
                           transaction_description: str = None,
                           transaction_clear_status: str = None,
                           transaction_approved: bool = None,
                           transaction_flag_color: str = None):
        # build a dictionary to house all the updates
        tdata = {}
        if transaction_account_id is not None:
            tdata["account_id"] = transaction_account_id.lower().strip()
        if transaction_date is not None:
            tdata["var_date"] = dtu.format_yyyymmdd(transaction_date)
        if transaction_amount is not None:
            tdata["amount"] = int(transaction_amount * 1000.0)
        if transaction_entity_id is not None:
            tdata["payee_id"] = transaction_entity_id.lower().strip()
        if transaction_category_id is not None:
            tdata["category_id"] = transaction_category_id.lower().strip()
        if transaction_description is not None:
            tdata["memo"] = transaction_description
        if transaction_clear_status is not None:
            tcs = transaction_clear_status.lower().strip()
            values = {
                "cleared": TransactionClearedStatus.CLEARED,
                "uncleared": TransactionClearedStatus.UNCLEARED,
                "reconciled": TransactionClearedStatus.RECONCILED
            }
            if tcs not in values:
                raise Exception("Invalid transaction clear status: \"%s\"" % tcs)
            tdata["cleared"] = values[tcs]
        if transaction_approved is not None:
            tdata["approved"] = transaction_approved
        if transaction_flag_color is not None:
            fc = transaction_flag_color.lower().strip()
            values = {
                "red": TransactionFlagColor.RED,
                "orange": TransactionFlagColor.ORANGE,
                "yellow": TransactionFlagColor.YELLOW,
                "green": TransactionFlagColor.GREEN,
                "blue": TransactionFlagColor.BLUE,
                "purple": TransactionFlagColor.PURPLE,
            }
            if fc not in values:
                raise Exception("Invalid transaction clear status: \"%s\"" % fc)
            tdata["flag_color"] = values[fc]

        # if no updates were made, return early
        if len(tdata) == 0:
            return None

        # otherwise, create a YNAB object to make the update
        et = ExistingTransaction.from_dict(tdata)
        ptw = PutTransactionWrapper.from_dict({
            "transaction": et
        })

        # send the update to YNAB
        api = self.api_transactions()
        return api.update_transaction(budget_id, transaction_id, ptw)

