# Base class for chore-based tasks.

# Imports
import os
import sys
from datetime import datetime
import threading
import time
import inspect

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob, TaskConfig
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession

# Base class for automotive-based tasks.
class TaskJob_Automotive(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        return self.get_project_by_name(todoist, "Automotive", color="red")

# Base class for medical-based tasks.
class TaskJob_Medical(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Medical")
        if proj is None:
            proj = todoist.add_project("Medical", color="blue")
        self.project = proj
        return proj

# Base class for finance-based tasks.
class TaskJob_Finance(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Finances")
        if proj is None:
            proj = todoist.add_project("Finances", color="olive_green")
        self.project = proj
        return proj

# Base class for house chores and maintenance.
class TaskJob_Household(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Household")
        if proj is None:
            proj = todoist.add_project("Household", color="yellow")
        self.project = proj
        return proj

# Base class for the grocery list.
class TaskJob_Groceries(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Groceries")
        if proj is None:
            proj = todoist.add_project("Groceries", color="green")
        self.project = proj
        return proj

# Base class for wedding-based tasks.
class TaskJob_Wedding(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Wedding")
        if proj is None:
            proj = todoist.add_project("Wedding", color="lavender")
        self.project = proj
        return proj


# ============================ Interview TaskJobs ============================ #
# Config object used for interview taskjobs.
class TaskJob_Interview_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("telegram_chat_id",  [str],      required=True),
        ]


# Base class for "interviews" (using the Telegram service to send queries to
# the user to get information and decide what to do.
class TaskJob_Interview(TaskJob):
    def __init__(self, service):
        super().__init__(service)
        self.thread_class = TaskJob_Interview_Thread
        self.config_class = TaskJob_Interview_Config
        self.refresh_rate = 120
        
    def get_config(self):
        # find and parse the config for this class
        config_fname = inspect.getfile(self.__class__).replace(".py", ".json")
        config_path = os.path.join(fdir, config_fname)
        config = self.config_class()
        config.parse_file(config_path)
        return config
 
    # Helper function that can be overridden by subclasses to determine if it's
    # time to update. Returns True if an update should be done.
    def is_ready_to_update(self, todoist, gcal):
        return False

    def update(self, todoist, gcal):
        # return early if it's not time to update yet
        if not self.is_ready_to_update(todoist, gcal):
            return False
        
        # if it is, spawn a thread of the configured class name to handle the
        # menu and everything else
        thrd = self.thread_class(self, todoist, gcal)
        thrd.start()
        return True

# An class that is spawned upon a successful call to `update()` (i.e.
# when `update()` return True. This is used to send and manage the
# communication with Telegram.
class TaskJob_Interview_Thread(threading.Thread):
    def __init__(self, taskjob, todoist, gcal):
        threading.Thread.__init__(self, target=self.run)
        self.taskjob = taskjob
        self.todoist = todoist
        self.gcal = gcal

    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.taskjob.service.config.telegram)
        s.login()
        return s
    
    # Main function for the thread. Must be overridden by the child class of
    # `TaskJob_interview`.
    def run(self):
        pass
    
    # Sends the menu to Telegram for the first time.
    def create_menu(self, menu: dict):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        config = self.taskjob.get_config()
        payload = {
            "chat_id": config.telegram_chat_id,
            "menu": menu,
        }
        r = telegram_session.post("/bot/send/menu", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to create menu via Telegram: %s" % \
               telegram_session.get_response_message(r)

        # parse the payload JSON in the response as a menu and return it (this
        # will contain the menu's ID, and other new information)
        created_menu = telegram_session.get_response_json(r)
        return created_menu
    
    # Updates a menu via Telegram.
    def update_menu(self, chat_id: str, message_id: str, menu: dict):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        config = self.taskjob.get_config()
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "menu": menu,
        }
        r = telegram_session.post("/bot/update/menu", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to update menu via Telegram: %s" % \
               telegram_session.get_response_message(r)

        # parse the payload JSON in the response as a menu and return it (this
        # will contain the menu's ID, and other new information)
        updated_menu = telegram_session.get_response_json(r)
        return updated_menu
    
    # Retrieves the menu from Telegram.
    def get_menu(self, menu_id: str, telegram_session=None):
        if telegram_session is None:
            telegram_session = self.get_telegram_session()

        # send a request to telegram to retrieve the menu object
        payload = {"menu_id": menu_id}
        r = telegram_session.post("/bot/get/menu", payload=payload)

        # check for a failure response; return early
        if not telegram_session.get_response_success(r):
            msg = telegram_session.get_response_message(r)
            self.taskjob.log("Failed to query Telegram for menu (ID: %s): %s" %
                             (menu_id, msg))
            return None

        # otherwise, extract the JSON payload and find the "menu" object
        # that was returned
        new_menu = telegram_session.get_response_json(r)
        return new_menu
    
    # Updates a message's text via Telegram.
    def update_message(self, chat_id: str, message_id: str, text: str):
        telegram_session = self.get_telegram_session()

        # send a request to telegram to update the message
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        r = telegram_session.post("/bot/update/message", payload=payload)

        # check for a failure response; return early
        if not telegram_session.get_response_success(r):
            msg = telegram_session.get_response_message(r)
            self.taskjob.log("Failed to update message via Telegram: %s" % msg)
            return None
 
    # Updates a message's text via Telegram.
    def remove_menu(self, chat_id: str, message_id: str):
        telegram_session = self.get_telegram_session()

        # send a request to telegram to remove the menu
        payload = {"chat_id": chat_id, "message_id": message_id}
        r = telegram_session.post("/bot/remove/menu", payload=payload)

        # check for a failure response; return early
        if not telegram_session.get_response_success(r):
            msg = telegram_session.get_response_message(r)
            self.taskjob.log("Failed to remove menu via Telegram: %s" % msg)
            return None
 
    # Takes in a Telegram menu object and repeatedly polls Telegram for
    # information on the menu. As soon as a change with the menu is seen, a new
    # menu object is returned.
    def await_menu_update(self, menu: dict, telegram_session=None) -> dict:
        if telegram_session is not None:
            telegram_session = self.get_telegram_session()

        # loop repeatedly until we see a change in the menu
        while True:
            new_menu = self.get_menu(menu["id"],
                                     telegram_session=telegram_session)
            
            # look at the menu's options and compare the old vs new
            # side-by-side
            options_len = len(menu["options"])
            assert len(new_menu["options"]) == options_len
            for idx in range(0, options_len):
                # compare the old and new selection counts. Was one changed? If
                # so, return immediately
                old = menu["options"][idx]
                new = new_menu["options"][idx]
                if old["selection_count"] != new["selection_count"]:
                    return new_menu

            # TODO - sleep more dynamically
            time.sleep(0.5)

