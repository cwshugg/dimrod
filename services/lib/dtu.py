# This module implements a number of helpful datetime-related functions.

# Imports
import re
from datetime import datetime, timedelta
from enum import Enum

# Simple enum to put names to numbers for datetime weekdays.
class Weekday(Enum):
    SUNDAY = 0
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6

def get_weekday(dt):
    value = int((dt.weekday() + 1) % 7)
    return Weekday(value)

# Takes in a weekday and returns the number of days 'dt' is away from the
# specified weekday. This looks FORWARDS for the next occurrence of the
# weekday.
# Returns 0 if the current weekday matches.
def get_days_until_weekday(dt, weekday):
   wd1 = get_weekday(dt).value
   wd2 = weekday.value

   if wd1 > wd2:
       return 7 + (wd2 - wd1)
   elif wd1 < wd2:
       return wd2 - wd1
   else:
       return 0

# Takes in a weekday and returns the number of days 'dt' is away from the
# weekday. This looks BACKWARDS for the most recent occurrence of the weekday.
# Returns 0 if the current weekday matches.
def get_days_since_weekday(dt, weekday):
    wd1 = get_weekday(dt).value
    wd2 = weekday.value
    if wd1 == wd2:
        return 0
    return 7 - get_days_until_weekday(dt, weekday)

# Returns True if the given day is a Saturday or Sunday.
def is_weekend(dt):
    return dt.weekday() in [5, 6]

# Returns True if the given day is Monday-Friday.
def is_weekday(dt):
    return dt.weekday() not in [5, 6]

# Returns True if the given time in the early morning before noon.
def is_morning(dt):
    return dt.hour >= 6 and dt.hour < 12

# Returns True if the given time is between noon and the evening.
def is_afternoon(dt):
    return dt.hour >= 12 and dt.hour < 17

# Returns True if the given time is between evening and night.
def is_evening(dt):
    return dt.hour >= 17 and dt.hour < 21

# Returns True if the given time is between night time and early morning.
def is_night(dt):
    return dt.hour >= 21 or dt.hour < 6

# Returns True if the time is between 9am and 5pm.
def is_workhours(dt):
    return dt.hour >= 9 and dt.hour < 17

# Returns True if the current season is spring. (Give or take a few days.)
def is_spring(dt):
    spring_start = datetime(dt.year, 3, 20)
    spring_end = datetime(dt.year, 6, 20)
    return dt.timestamp() >= spring_start.timestamp() and \
           dt.timestamp() < spring_end.timestamp()

# Returns True if the current season is summer. (Give or take a few days.)
def is_summer(dt):
    summer_start = datetime(dt.year, 6, 20)
    summer_end = datetime(dt.year, 9, 20)
    return dt.timestamp() >= summer_start.timestamp() and \
           dt.timestamp() < summer_end.timestamp()

# Returns True if the current season is fall. (Give or take a few days.)
def is_fall(dt):
    fall_start = datetime(dt.year, 9, 20)
    fall_end = datetime(dt.year, 12, 20)
    return dt.timestamp() >= fall_start.timestamp() and \
           dt.timestamp() < fall_end.timestamp()

# Returns True if the current season is winter. (Give or take a few days.)
def is_winter(dt):
    winter_start = datetime(dt.year, 12, 20) if dt.month > 3 else datetime(dt.year, 1, 1)
    winter_end = datetime(dt.year, 12, 31) if dt.month > 3 else datetime(dt.year, 3, 20)
    return dt.timestamp() >= winter_start.timestamp() and \
           dt.timestamp() < winter_start.timestamp()

# Given a datetime, this returns the datetime representing the final day of
# that datetime's month.
# Special thanks to this clever solution:
#   https://stackoverflow.com/questions/42950/get-the-last-day-of-the-month
def get_last_day_of_month(dt):
    next_month = dt.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)

# Creates and returns a copy of the given datetime with the time set to
# 11:59pm.
def set_time_end_of_day(dt):
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)

# Adds 'mult' numbers of minutes to the given datetime.
def add_minutes(dt, mult=1):
    return dt.fromtimestamp(dt.timestamp() + (mult * 60))

# Adds 'mult' numbers of hours to the given datetime.
def add_hours(dt, mult=1):
    return dt.fromtimestamp(dt.timestamp() + (mult * 3600))

# Adds 'mult' numbers of days to the given datetime.
def add_days(dt, mult=1):
    return dt.fromtimestamp(dt.timestamp() + (mult * 86400))

# Adds 'mult' numbers of weeks to the given datetime.
def add_weeks(dt, mult=1):
    return dt.fromtimestamp(dt.timestamp() + (mult * 604800))

# Returns the difference between the two datetimes in minutes.
def diff_in_minutes(dt1, dt2):
    return (dt1.timestamp() - dt2.timestamp()) / 60

# Returns the difference between the two datetimes in hours.
def diff_in_hours(dt1, dt2):
    return (dt1.timestamp() - dt2.timestamp()) / 3600

# Returns the difference between the two datetimes in days.
def diff_in_days(dt1, dt2):
    return (dt1.timestamp() - dt2.timestamp()) / 86400

# Returns the difference between the two datetimes in weeks.
def diff_in_weeks(dt1, dt2):
    return (dt1.timestamp() - dt2.timestamp()) / 604800

# Returns True if the two datetimes share the same year.
def has_same_year(dt1, dt2):
    return dt1.year == dt2.year

# Returns True if the two datetimes share the same month.
def has_same_month(dt1, dt2):
    return dt1.month == dt2.month

# Returns True if the two datetimes share the same day.
def has_same_day(dt1, dt2):
    return dt1.day == dt2.day

# Returns True if the two datetimes share the same year AND month.
def has_same_year_month(dt1, dt2):
    return dt1.year == dt2.year and \
           dt1.month == dt2.month

# Returns True if the two datetimes share the same year, month, AND day.
def has_same_year_month_day(dt1, dt2):
    return dt1.year == dt2.year and \
           dt1.month == dt2.month and \
           dt1.day == dt2.day

# Parses a YYYY-MM-DD string and returns the year, month, and day, in an array
# of three integers [year, month, day]. Returns None if parsing failed.
def parse_yyyymmdd(text: str):
    result = None
    
    # attempt parsing with multiple delimeters
    delimeters = ["-", "/", "."]
    for delim in delimeters:
        try:
            d = datetime.strptime(text, "%Y" + delim + "%m" + delim + "%d")
            result = [d.year, d.month, d.day]
        except:
            pass
    return result

# Returns a weekday enum value on the given text. Returns None if the string
# isn't recognized.
def parse_weekday(text: str):
    tl = text.strip().lower()
    weekdays = [
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday"
    ]
    for i in range(len(weekdays)):
        if tl in weekdays[i]:
            return Weekday(i)
    return None

# Parses a suffixed time offset string (ex: "1w", "2d", "3h", "4m"). Returns 0
# if nothing is recognized.
def parse_time_offset(text: str):
    suffixes = {
        "w": 604800,        # one week (in seconds)
        "d": 86400,         # one day (in seconds)
        "h": 3600,          # one hour (in seconds)
        "m": 60             # one minute (in seconds)
    }
    for suffix in suffixes:
        if not text.endswith(suffix):
            continue
        # parse digits from the string and return the offset
        multiplier = float(re.findall("\d+", text)[0])
        return multiplier * suffixes[suffix]
    return 0.0

# Attempts to parse timestamps such as "9pm" or "10:30am".
# Returns an (hour, minute) tuple.
def parse_time_clock(text: str):
    text = text.strip().lower()

    # if the string doesn't end in AM/PM, return None
    am = text.endswith("am")
    pm = text.endswith("pm")
    if not am and not pm:
        return None
    text = text.replace("am", "") if am else text.replace("pm", "")

    # if there's a colon, split the string into hour and minute sections
    pieces = text.split(":")
    hour_str = pieces[0]
    minute_str = pieces[1] if len(pieces) > 1 else "0"

    # parse each string accordingly
    try:
        hour = int(hour_str)
        minute = int(minute_str)

        # account for PM time
        if pm and hour < 12:
            hour += 12
        return (hour, minute)
    except Exception as e:
        return None

