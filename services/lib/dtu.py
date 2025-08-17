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

# Returns the weekday, as an enum.
def get_weekday(dt):
    value = int((dt.weekday() + 1) % 7)
    return Weekday(value)

# Returns the weekday, as a string.
def get_weekday_str(dt):
    # Use `title()` to uppercase only the first letter
    return get_weekday(dt).name.title()

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

def is_sunday(dt):
    return get_weekday(dt) == Weekday.SUNDAY

def is_monday(dt):
    return get_weekday(dt) == Weekday.MONDAY

def is_tuesday(dt):
    return get_weekday(dt) == Weekday.TUESDAY

def is_wednesday(dt):
    return get_weekday(dt) == Weekday.WEDNESDAY

def is_thursday(dt):
    return get_weekday(dt) == Weekday.THURSDAY

def is_friday(dt):
    return get_weekday(dt) == Weekday.FRIDAY

def is_saturday(dt):
    return get_weekday(dt) == Weekday.SATURDAY

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

# Returns True if the given time is exactly at midnight (hour 0, minute 0,
# second 0).
def is_exact_midnight(dt):
    return dt.hour == 0 and dt.minute == 0 and dt.second == 0

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
# 12:00am.
def set_time_beginning_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

# Creates and returns a copy of the given datetime with the time set to
# 11:59pm.
def set_time_end_of_day(dt):
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)

# Adds 'mult' number of seconds to the given datetime.
def add_seconds(dt, mult=1):
    return dt.fromtimestamp(dt.timestamp() + mult)

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

# Returns the difference between the two datetimes in seconds.
def diff_in_seconds(dt1, dt2):
    # make copies of the datetimes, with both timezones removed, so we can do a
    # clean subtraction
    notz_dt1 = dt1.replace(tzinfo=None)
    notz_dt2 = dt2.replace(tzinfo=None)
    diff = notz_dt1 - notz_dt2
    return diff.total_seconds()

# Returns the difference between the two datetimes in minutes.
def diff_in_minutes(dt1, dt2):
    return diff_in_seconds(dt1, dt2) / 60

# Returns the difference between the two datetimes in hours.
def diff_in_hours(dt1, dt2):
    return diff_in_seconds(dt1, dt2) / 3600

# Returns the difference between the two datetimes in days.
def diff_in_days(dt1, dt2):
    return diff_in_seconds(dt1, dt2) / 86400

# Returns the difference between the two datetimes in weeks.
def diff_in_weeks(dt1, dt2):
    return diff_in_seconds(dt1, dt2) / 604800

# Returns two numbers in an array:
#   [remaining_diff_in_seconds, diff_in_minutes]
def diff_in_seconds_minutes(dt1, dt2):
    mins = int(diff_in_minutes(dt1, dt2))
    secs = diff_in_seconds(dt1, dt2)

    # if there was at least one minute of difference, subtract out the amount
    # from the total seconds diff to get the remaining seconds diff
    if mins > 0:
        secs -= mins * 60
    return [secs, mins]

# Returns three numbers in an array:
#   [
#       remaining_diff_in_seconds,
#       remaining_diff_in_minutes,
#       diff_in_hours
#   ]
def diff_in_seconds_minutes_hours(dt1, dt2):
    hours = int(diff_in_hours(dt1, dt2))
    [secs, mins] = diff_in_seconds_minutes(dt1, dt2)
    
    # if there was at least one hour's worth of time difference, subtract out
    # those minutes from the total number of minutes
    if hours > 0:
        mins -= hours * 60

    return [secs, mins, hours]

# Returns True if the two datetimes share the same year.
def has_same_year(dt1, dt2):
    return dt1.year == dt2.year

# Returns True if the two datetimes share the same month.
def has_same_month(dt1, dt2):
    return dt1.month == dt2.month

# Returns True if the two datetimes share the same day.
def has_same_day(dt1, dt2):
    return dt1.day == dt2.day

# Returns True if the two datetimes are on the same calendar month.
def has_same_exact_day(dt1, dt2):
    return has_same_year(dt1, dt2) and \
           has_same_month(dt1, dt2)

# Returns True if the two datetimes are on the same calendar day.
def has_same_exact_day(dt1, dt2):
    return has_same_year(dt1, dt2) and \
           has_same_month(dt1, dt2) and \
           has_same_day(dt1, dt2)

# Returns True if the two datetimes share the same year AND month.
def has_same_year_month(dt1, dt2):
    return dt1.year == dt2.year and \
           dt1.month == dt2.month

# Returns True if the two datetimes share the same year, month, AND day.
def has_same_year_month_day(dt1, dt2):
    return dt1.year == dt2.year and \
           dt1.month == dt2.month and \
           dt1.day == dt2.day

# Returns a string with the "YYYY-MM-DD" format.
def format_yyyymmdd(dt):
    return dt.strftime("%Y-%m-%d")

# Returns a string with the "YYYY-MM-DD HH:MM:SS" (24 hour) format.
def format_yyyymmdd_hhmmss_24h(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

# Returns a string with the "YYYY-MM-DD HH:MM:SS AM/PM" (12 hour) format.
def format_yyyymmdd_hhmmss_12h(dt):
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")

# Parses a YYYY-MM-DD string and returns the year, month, and day, in an array
# of three integers [year, month, day]. Returns None if parsing failed.
def parse_yyyymmdd(text: str):
    # attempt parsing with multiple delimeters
    delimeters = ["-", "/", "."]
    for delim in delimeters:
        try:
            d = datetime.strptime(text, "%Y" + delim + "%m" + delim + "%d")
            return [d.year, d.month, d.day]
        except:
            pass
    return None

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
        re_result = re.findall("\d+", text)
        if len(re_result) > 0:
            multiplier = float(re_result[0])
            return multiplier * suffixes[suffix]
    return None

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

# Parses a single datetime from a variety of formats. Returns a datetime
# object, or None, depending on what was found in the given string arguments.
#
# If `now` is provided (a datetime object), the parsed datetime will use `now`
# as a reference point from which to jump. (By default, `now` becomes
# `datetime.now()`.) For example, if the args `["1h" "20m"]` were provided, the
# resulting datetime object would be one hour and twenty minutes after `now`.
def parse_datetime(args: list, now=None):
    # Returns a weekday number based on the given text. Returns None if the
    # string isn't recognized.
    def p_weekday(text: str):
        result = parse_weekday(text)
        return None if result is None else result.value + 1
    
    # Converts Python's monday-first weekday encoding to my sunday-first
    # encoding.
    def g_weekday(dt: datetime):
        return get_weekday(dt).value + 1
    
    # if `now` was not specified, default to the current datetime
    if now is None:
        now = datetime.now()

    # iterate through the arguments, one at a time, searching for date and time
    # specifications
    dt = None
    for arg in args:
        # look for a YYYY-MM-DD date stamp
        datestamp = parse_yyyymmdd(arg)
        if datestamp is not None:
            # fill out the starting and ending datetimes with these, depending
            # on the order received
            dt = datetime(datestamp[0], datestamp[1], datestamp[2],
                          hour=0, minute=0, second=0, microsecond=0)
            continue

        # look for mention of a weekday
        wd = p_weekday(arg)
        if wd is not None:
            # increase the current datetime until it lines up with the
            # specified weekday
            dt = add_days(now, 1)
            while g_weekday(dt) != wd:
                dt = add_days(dt, 1)
            continue
        
        # look for AM/PM suffixed timestamps
        clocktime = parse_time_clock(arg)
        if clocktime is not None:
            h = clocktime[0]
            m = clocktime[1]
            
            # if `dt` has not yet been set, use the current datetime
            if dt is None:
                dt = now

            # compute an offset based on the hour and minute (jump to the next
            # day if 'dt' is still set to the current day and the hour/minute
            # have already passed today)
            offset = 0.0
            if dt.hour > h and has_same_exact_day(dt, now):
                offset += 86400
            offset += (h - dt.hour) * 3600
            offset += (m - dt.minute) * 60
            dt = add_seconds(dt, offset)
            continue

        # look for suffixed time offsets ("1d", "2h", "3m", etc.)
        offset = parse_time_offset(arg)
        if offset is not None:
            # set `dt` to the current time if it hasn't been set yet
            if dt is None:
                dt = now
            dt = add_seconds(dt, offset)
            continue
    return dt

