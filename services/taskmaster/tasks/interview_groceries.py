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
from tasks.base import *
import lib.dtu as dtu
from lib.config import Config, ConfigField

class TaskJob_Interview_Groceries_ItemConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",     [str], required=True),
            ConfigField("question", [str], required=True),
        ]

class TaskJob_Interview_Groceries_Config(TaskJob_Interview_Config):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("grocery_items", [TaskJob_Interview_Groceries_ItemConfig], required=True),
        ]

class TaskJob_Interview_Groceries(TaskJob_Interview, TaskJob_Groceries):
    def __init__(self, service):
        super().__init__(service)
        self.thread_class = TaskJob_Interview_Groceries_Thread
        self.config_class = TaskJob_Interview_Groceries_Config

    def is_ready_to_update(self, todoist, gcal):
        # if this was done within the past week, don't do it right now
        last_success = self.get_last_success_datetime()
        now = datetime.now()
        if last_success is not None and dtu.diff_in_days(now, last_success) <= 6:
            return False

        # if it's not Sunday, don't do it
        if dtu.get_weekday(now) != dtu.Weekday.SUNDAY:
            return False

        # if it's not the morning, don't do it
        if now.hour not in range(6, 10):
            return False

        return True

class TaskJob_Interview_Groceries_Thread(TaskJob_Interview_Thread):
    def __init__(self, taskjob, todoist, gcal):
        super().__init__(taskjob, todoist, gcal)
        self.item_idx = 0
        self.items_added = []
        self.menu = {
            "title": "THIS WILL BE SET DYNAMICALLY",
            "options": [
                {
                    "title": "✅ Yes",
                },
                {
                    "title": "❌ No",
                },
            ],
            "timeout": 3600 * 8
        }

    def run(self):
        config = self.taskjob.get_config()

        # if it's time to update, create a menu and send it to Telegram
        items = config.grocery_items
        self.set_menu_title(self.menu, self.item_idx, items)
        menu = self.create_menu(self.menu)

        # wait for a change in the menu (i.e. wait for the user to press a
        # button or something), then pass the updated menu into another
        # function. That function will return a new menu to send to the user,
        # at which point we'll update it
        #
        # do this forever, until the handler function decides there are no more
        # menus to send, and returns None
        ts = self.get_telegram_session()
        while menu is not None:
            updated_menu = self.await_menu_update(menu, telegram_session=ts)
            menu = self.handle_updated_menu(items, menu, updated_menu)

    def set_menu_title(self, menu: dict, idx: int, items: list):
        item = items[idx]
        menu["title"] = "Groceries Interview - (%d/%d)\n\n" \
                        "%s" % \
                        ((idx + 1), len(items), item.question)
        return menu
    
    # Helper function that must be handled by the subclass. It should return
    # either a new/updated menu, or None.
    def handle_updated_menu(self, items: list, menu: dict, updated_menu: dict):
        yes_old = menu["options"][0]
        yes_new = updated_menu["options"][0]
        no_old = menu["options"][1]
        no_new = updated_menu["options"][1]

        # compare the selection counts for the YES and NO answers to determine
        # which option the user selected
        if yes_new["selection_count"] > yes_old["selection_count"]:
            new_item = TaskConfig()
            new_item.parse_json({
                "title": items[self.item_idx].name,
                "content": "",
            })
            self.add_grocery_item(new_item)
            self.items_added.append(items[self.item_idx].name)

        # increase the item index; have we exceeded the number of items? If
        # so, we're done; return None
        self.item_idx += 1
        if self.item_idx >= len(items):
            # remove the menu from the message
            self.remove_menu(menu["telegram_msg_info"]["chat"]["id"],
                             menu["telegram_msg_info"]["id"])

            # update the message to indicate that all questions are done
            msg = "Groceries interview complete."
            if len(self.items_added) > 0:
                msg += " I've added the following items to the grocery list:\n\n"
                for item in self.items_added:
                    msg += " • %s\n" % item

            # update the message
            self.update_message(menu["telegram_msg_info"]["chat"]["id"],
                                menu["telegram_msg_info"]["id"],
                                msg)
            return None

        # otherwise, update the menu title to show the next item, and
        # update the menu
        self.set_menu_title(updated_menu, self.item_idx, items)
        self.update_message(updated_menu["telegram_msg_info"]["chat"]["id"],
                            updated_menu["telegram_msg_info"]["id"],
                            updated_menu["title"])
        return self.update_menu(updated_menu["telegram_msg_info"]["chat"]["id"],
                                updated_menu["telegram_msg_info"]["id"],
                                updated_menu)
                                

    # Adds a new task under the `Groceries` Todoist project.
    def add_grocery_item(self, t: TaskConfig):
        proj = self.taskjob.get_project(self.todoist)
        self.todoist.add_task(t.title,
                              t.get_content(),
                              project_id=proj.id,
                              priority=t.priority,
                              labels=t.labels)

