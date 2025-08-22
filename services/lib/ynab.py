# This module provides a basic wrapper around the official YNAB (You Need A
# Budget) Python SDK.

# Imports
import os
import sys
from datetime import datetime
import json

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# YNAB imports
import ynab
from ynab.models.transaction_cleared_status import TransactionClearedStatus
from ynab.models.transaction_flag_color import TransactionFlagColor
from ynab.models.save_transaction_with_id_or_import_id import SaveTransactionWithIdOrImportId
from ynab.models.patch_transactions_wrapper import PatchTransactionsWrapper

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# An object used for updating an existing YNAB transaction.
class YNABTransactionUpdate(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",                      [str],      required=True),
            ConfigField("update_account_id",       [str],      required=False, default=None),
            ConfigField("update_entity_id",        [str],      required=False, default=None),
            ConfigField("update_amount",           [float],    required=False, default=None),
            ConfigField("update_date",             [datetime], required=False, default=None),
            ConfigField("update_category_id",      [str],      required=False, default=None),
            ConfigField("update_description",      [str],      required=False, default=None),
            ConfigField("update_cleared_status",   [str],      required=False, default=None),
            ConfigField("update_approved",         [bool],     required=False, default=None),
            ConfigField("update_flag_color",       [str],      required=False, default=None),
            
            # Fields that are *not* used for updating, but strictly used for
            # temporarily storing information in the object:
            ConfigField("transaction",             [any],      required=False, default=None),
            ConfigField("account",                 [any],      required=False, default=None),
            ConfigField("category",                [any],      required=False, default=None),
        ]

    def __str__(self):
        return json.dumps(self.to_update_dict())
    
    def has_updates(self):
        return self.update_account_id is not None or \
               self.update_entity_id is not None or \
               self.update_amount is not None or \
               self.update_date is not None or \
               self.update_category_id is not None or \
               self.update_description is not None or \
               self.update_cleared_status is not None or \
               self.update_approved is not None or \
               self.update_approved is not None
    
    # Returns a dictionary containing all YNAB-friendly update fields.
    def to_update_dict(self):
        tdata = {}

        # conditionally add fields
        if self.update_account_id is not None:
            tdata["account_id"] = self.update_account_id.lower().strip()
        if self.update_entity_id is not None:
            tdata["payee_id"] = self.update_entity_id.lower().strip()
        if self.update_amount is not None:
            tdata["amount"] = int(self.update_amount * 1000.0)
        if self.update_date is not None:
            tdata["var_date"] = dtu.format_yyyymmdd(self.update_date)
        if self.update_category_id is not None:
            tdata["category_id"] = self.update_category_id.lower().strip()
        if self.update_description is not None:
            tdata["memo"] = self.update_description
        if self.update_cleared_status is not None:
            tcs = self.update_cleared_status.lower().strip()
            values = {
                "cleared": TransactionClearedStatus.CLEARED,
                "uncleared": TransactionClearedStatus.UNCLEARED,
                "reconciled": TransactionClearedStatus.RECONCILED
            }
            if tcs not in values:
                raise Exception("Invalid transaction clear status: \"%s\"" % tcs)
            tdata["cleared"] = values[tcs]
        if self.update_approved is not None:
            tdata["approved"] = self.update_approved
        if self.update_flag_color is not None:
            fc = self.update_flag_color.lower().strip()
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

        # if no updates are being made, there is no point building the object
        if len(tdata) == 0:
            return None
        return tdata

    # Returns a YNAB API object with which a transaction can be updated.
    # If no updates are actually being made, `None` is returned
    def to_update_ynab_obj(self):
        d = self.to_update_dict()
        if d is None:
            return None
                
        # create the YNAB object and return
        d.update({"id": self.id})
        return SaveTransactionWithIdOrImportId.from_dict(d)

# A wrapper class for a YNAB transaction object to make working with its data
# easier.
class YNABTransactionInfo:
    def __init__(self, transaction):
        self.transaction = transaction

    def get_id(self):
        return self.transaction.id

    def get_account_id(self):
        return self.transaction.account_id

    def get_account_name(self):
        return self.transaction.account_name

    def get_payee_id(self):
        return self.transaction.payee_id

    def get_payee_name(self):
        if self.transaction.payee_name is not None:
            return self.transaction.payee_name
        if self.transaction.import_payee_name is not None:
            return self.transaction.import_payee_name
        if self.transaction.import_payee_name_original is not None:
            return self.transaction.import_payee_name_original
        return None

    def get_category_id(self):
        return self.transaction.category_id

    def get_date(self):
        return self.transaction.var_date
    
    def get_amount(self):
        return float(self.transaction.amount) / 1000.0
    
    def get_description(self):
        if self.transaction.memo is None or \
           len(self.transaction.memo) == 0:
            return None
        return self.transaction.memo
    
    def get_approved(self):
        return self.transaction.approved

    def get_cleared_status(self):
        return self.transaction.cleared

    def get_flag_color(self):
        return self.transaction.flag_color
    
    def __str__(self):
        r = "Date=\"%s\" " % dtu.format_yyyymmdd(self.get_date())
        r += "Amount=\"%.2f\" " % self.get_amount()
        r += "Entity=\"%s\"" % self.get_payee_name()
        if self.get_description() is not None:
            r += " Description=\"%s\"" % self.get_description()
        return r

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
            result.append(YNABTransactionInfo(t))

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
    
    # Retrieves both unapproved and uncategorized transactions.
    def get_transactions_unapproved_uncategorized(self,
                                                  budget_id: str,
                                                  since_date: datetime = None):
        uats = self.get_transactions_unapproved(budget_id, since_date=since_date)
        ucts = self.get_transactions_uncategorized(budget_id, since_date=since_date)
        transactions = {}

        # iterate through ALL transactions and build a combined dictionary,
        # such that there are no duplicates
        for t in uats:
            if str(t.get_id()) not in transactions:
                transactions[str(t.get_id())] = t
        for t in ucts:
            if str(t.get_id()) not in transactions:
                transactions[str(t.get_id())] = t

        # return the list of transactions
        return transactions.values()
    
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
            result.append(YNABTransactionInfo(t))

        return result
    
    # Accepts a list of `YNABTransactionUpdate` objects and attempts to submit
    # updates to the YNAB API for all of them.
    def update_transactions(self,
                            budget_id: str,
                            updates: list):
        # iterate through the updates and build a list of YNAB update objects
        objs = []
        for update in updates:
            obj = update.to_update_ynab_obj()
            if obj is None:
                continue
            objs.append(obj)

        # if none of the updates made actually contained new changes, return
        # early
        if len(objs) == 0:
            return None

        # otherwise, create a wrapper object
        wrapper = PatchTransactionsWrapper.from_dict({
            "transactions": objs
        })

        # attempt to send the updates to YNAB
        api = self.api_transactions()
        return api.update_transactions(budget_id, wrapper)

    # Updates a single YNAB transaction.
    def update_transaction(self,
                           budget_id: str,
                           update: YNABTransactionUpdate):
        return self.update_transactions(budget_id, [update])

