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
from tasks.services.base import *
import lib.dtu as dtu
from lib.wyze import Wyze

# A taskjob that routinely checks my current Wyze API key for expiration.
class TaskJob_Services_WyzeAPIKey(TaskJob_Services):
    def init(self):
        self.refresh_rate = 3600 * 24 # check every day

    def update(self, todoist, gcal):
        proj = self.get_project(todoist)
        sect = self.get_section_by_name(todoist, proj.id, "Maintenance")

        # attempt to initialize a Wyze API object. If it fails to login, we
        # assume the API key is invalid.
        wyze = Wyze(self.service.config.wyze)
        try:
            wyze.login()
            # on success, return early from the function
            return False
        except Exception as e:
            self.log("Failed to log into Wyze account: %s" % str(e))

        # set up a TaskConfig object for the task
        content_fname = __file__.replace(".py", ".md")
        t = TaskConfig()
        t.parse_json({
            "title": "Regenerate Wyze API Key",
            "content": os.path.join(fdir, content_fname)
        })

        # retrieve the task (if it exists) and select an appropriate due date
        now = datetime.now()
        task = todoist.get_task_by_title(t.title, project_id=proj.id, section_id=sect.id)
        due = dtu.add_days(now, dtu.get_days_until_weekday(now, dtu.Weekday.SATURDAY))
        due = dtu.set_time_end_of_day(due)

        # if the task doesn't exist, create it
        if task is None:
            todoist.add_task(t.title, t.get_content(),
                             project_id=proj.id, section_id=sect.id,
                             due_datetime=due, priority=t.priority, labels=t.labels)
            return True

        # otherwise, update the task's due date and refresh the content
        todoist.update_task(task.id, body=t.get_content(), due_datetime=due)
        return True

