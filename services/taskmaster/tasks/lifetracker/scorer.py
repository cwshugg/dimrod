# Imports
import os
import sys
from datetime import datetime
import re

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from tasks.lifetracker.base import *
import lib.dtu as dtu
from lib.config import Config, ConfigField

class ScoringTimeRange:
    def __init__(self, start: datetime, end: datetime):
        self.score = 0
        self.start = start
        self.end = end

    def get_score_field_name(self):
        return "score_%s_%s" % \
               (dtu.format_yyyymmdd(self.start), dtu.format_yyyymmdd(self.end))

class TaskJob_LifeTracker_Scorer(TaskJob_LifeTracker):
    # Overridden initialization function.
    def init(self):
        super().init()
        self.config_name = "lifetracker_main.json"
        self.refresh_rate = 520000 # about 6 days

    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        tracker = self.get_tracker()
        
        # is it a Saturday night? If not, don't execute
        now = datetime.now()
        if not dtu.is_saturday(now) or not dtu.is_night(dtu):
            return False

        # first, collect a list of scorable metrics; that is, metrics whose
        # values all do NOT have zero for their possible scores
        smetrics = []
        for metric in tracker.metrics:
            if metric.has_nonzero_scores():
                smetrics.append(metric)

        lw_start = dtu.set_time_beginning_of_day(dtu.add_weeks(now, -1))
        lw_end = dtu.set_time_end_of_day(dtu.add_days(lw_start, 6))

        # build a set of timerange objects with which we'll compute scores
        timeranges = []
        timeranges.append(ScoringTimeRange( # LAST WEEK
            lw_start,
            lw_end
        ))
        timeranges.append(ScoringTimeRange( # TWO WEEKS AGO
            dtu.set_time_beginning_of_day(dtu.add_weeks(lw_start, -1)),
            dtu.set_time_end_of_day(dtu.add_weeks(lw_end, -1))
        ))
        timeranges.append(ScoringTimeRange( # THREE WEEKS AGO
            dtu.set_time_beginning_of_day(dtu.add_weeks(lw_start, -2)),
            dtu.set_time_end_of_day(dtu.add_weeks(lw_end, -2))
        ))
        timeranges.append(ScoringTimeRange( # FOUR WEEKS AGO
            dtu.set_time_beginning_of_day(dtu.add_weeks(lw_start, -3)),
            dtu.set_time_end_of_day(dtu.add_weeks(lw_end, -3))
        ))
        
        # iterate through all metrics in the tracker. For each metric, we'll
        # determine scores based on what's in the metric database
        for metric in smetrics:
            # for all time ranges, compute a score
            for tr in timeranges:
                tr_entries = tracker.get_metric_entries_by_timestamp(metric, tr.start, tr.end)
                score = self.score_metric_entries(metric, tr_entries)
                setattr(metric, tr.get_score_field_name(), score)
                tr.score += score

        # next, we'll build a telegram message to report the score
        msg = "<b>HAL Score Update!</b>\n"

        # report the current score from last week
        for tr in timeranges:
            msg += "• Score (%s - %s): <code>%d</code>\n" % \
                (dtu.format_yyyymmdd(tr.start), dtu.format_yyyymmdd(tr.end), tr.score)
        msg += "\n"

        # report the full breakdown of scores
        msg += "<b>Breakdown of Metrics:</b>\n"
        for metric in smetrics:
            # take the first sentence of the metric title
            first_sentence = re.split(r"(?<=[?!\.])\s*", metric.title)[0]

            # get a string list of all the scores for this metric from all the
            # timeranges
            score_str = ""
            timeranges_len = len(timeranges)
            for (i, tr) in enumerate(timeranges):
                score = getattr(metric, tr.get_score_field_name())
                score_str += "%s<code>%d</code>%s" % (
                    "(" if i == 1 else "",
                    score,
                    ")" if i == (timeranges_len - 1) else ", "
                )

            msg += "• <i>%s</i> - %s\n" % (first_sentence, score_str)


        # send the message and return
        self.send_message(tracker, msg)
        return True
    
    # Computes a score for the given list of metric entries, with the provided
    # metric to match it.
    def score_metric_entries(self, metric: LifeMetric, entries: list):
        total_score = 0

        # iterate across all entries
        for entry in entries:
            entry_score = 0

            # examine all values in the entry
            for value in metric.values:
                # use the SQLite3 column names to retrieve the proper class
                # fields for this particular value
                count_name = value.get_sqlite3_column_name_selection_count()
                score_name = value.get_sqlite3_column_name_score_per_count()
                count = getattr(entry, count_name)
                possible_score = getattr(entry, score_name)

                # compute the score: the number of times the value was
                # selected, multiplied by the possible score for each selection
                score = possible_score * count
                entry_score += score

            total_score += entry_score
        
        return total_score

