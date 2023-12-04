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
from tasks.chores import *
import lib.dtu as dtu

class TaskJob_Chores_Automotive_Insurance(TaskJob_Chores_Automotive):
    def update(self, todoist):
        proj = self.get_project(todoist)
        sect = self.get_section(todoist)

        # set up a TaskConfig object for the task
        content_fname = __file__.replace(".py", ".md")
        t = TaskConfig()
        t.parse_json({
            "title": "Review car insurance",
            "content": os.path.join(fdir, content_fname)
        })

        # if this task succeeded recently (within the past month), don't
        # proceed any further (the task must have already been added)
        last_success = self.get_last_success_datetime()
        now = datetime.now()
        if last_success is not None and dtu.diff_in_days(now, last_success) <= 30:
            return False
    
        # only update on certain days
        if now.day not in range(3, 8):
            return False
        # update this every six months
        if now.month not in [6, 12]:
            return False
        
        # retrieve the task (if it exists) and select an appropriate due date
        task = todoist.get_task_by_title(t.title, project_id=proj.id, section_id=sect.id)
        due = dtu.set_time_end_of_day(dtu.get_last_day_of_month(now))

        # if the task doesn't exist, create it
        if task is None:
            todoist.add_task(t.title, t.get_content(),
                             project_id=proj.id, section_id=sect.id,
                             due_datetime=due, priority=t.priority, labels=t.labels)
            return True

        # otherwise, update the task's due date and refresh the content
        todoist.update_task(task.id, body=t.get_content(), due_datetime=due)
        return True

