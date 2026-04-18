# This module implements a number of helpful datetime-related functions and
# types, including enums for weekdays and months, a general-purpose
# DatetimeTrigger for schedule matching, and a variety of parsing/formatting
# helpers.

# Imports
import os
import sys
import re
from datetime import datetime, timedelta, date, time
from enum import Enum

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField


class Weekday(Enum):
    """Simple enum to put names to numbers for datetime weekdays."""
    SUNDAY = 0
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6


class Month(Enum):
    """Simple enum to put names to numbers for calendar months."""
    JANUARY = 1
    FEBRUARY = 2
    MARCH = 3
    APRIL = 4
    MAY = 5
    JUNE = 6
    JULY = 7
    AUGUST = 8
    SEPTEMBER = 9
    OCTOBER = 10
    NOVEMBER = 11
    DECEMBER = 12


class DatetimeTrigger(Uniserdes):
    """A general-purpose datetime trigger that matches datetimes against a set
    of field constraints. Each field is a list acting as a filter: an empty
    list is a wildcard (matches any value), a non-empty list matches if the
    datetime component equals any value in the list.

    All non-empty fields must be satisfied simultaneously (AND between fields),
    while any value within a single field is sufficient (OR within a field).

    Fields:
        years    — list of int: specific years to match (e.g. [2024, 2025]).
        months   — list of Month enum: calendar months (JANUARY..DECEMBER).
        days     — list of int: day of month (1-31); negative values count
                   from end of month (-1 = last day, -2 = second-to-last).
        weekdays — list of Weekday enum: days of week (SUNDAY..SATURDAY).
        hours    — list of int: hour of day, 0-23 (24-hour clock).
        minutes  — list of int: minute of hour, 0-59.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("years",    [list], required=False, default=[]),
            UniserdesField("months",   [list], required=False, default=[]),
            UniserdesField("days",     [list], required=False, default=[]),
            UniserdesField("weekdays", [list], required=False, default=[]),
            UniserdesField("hours",    [list], required=False, default=[]),
            UniserdesField("minutes",  [list], required=False, default=[]),
        ]

    def post_parse_init(self):
        """Converts raw integer values in months and weekdays lists to their
        respective enum types, then validates all field values.
        """
        # Convert raw month integers/strings to Month enum values.
        if self.months:
            converted = []
            for m in self.months:
                if isinstance(m, Month):
                    converted.append(m)
                elif isinstance(m, int):
                    converted.append(Month(m))
                elif isinstance(m, str):
                    converted.append(Month[m.upper()])
                else:
                    self.check(False,
                        "DatetimeTrigger 'months' entry must be an int "
                        "or Month enum, got: %s" % type(m).__name__)
            self.months = converted

        # Convert raw weekday integers/strings to Weekday enum values.
        if self.weekdays:
            converted = []
            for w in self.weekdays:
                if isinstance(w, Weekday):
                    converted.append(w)
                elif isinstance(w, int):
                    converted.append(Weekday(w))
                elif isinstance(w, str):
                    converted.append(Weekday[w.upper()])
                else:
                    self.check(False,
                        "DatetimeTrigger 'weekdays' entry must be an "
                        "int or Weekday enum, got: %s" % type(w).__name__)
            self.weekdays = converted

        self.check_fields()

    def check_fields(self):
        """Validates all trigger field values are within legal ranges.

        Raises Exception (via ``self.check()``) on invalid values.
        """
        # years: any int is valid, no range restriction
        for y in self.years:
            self.check(isinstance(y, int),
                "DatetimeTrigger 'years' entry must be an int, "
                "got: %s" % type(y).__name__)

        # months: must be valid Month enum values (already converted)
        for m in self.months:
            self.check(isinstance(m, Month),
                "DatetimeTrigger 'months' entry must be a Month "
                "enum, got: %s" % type(m).__name__)

        # days: each must be int in 1-31 or -31 to -1 (NOT zero)
        for d in self.days:
            self.check(isinstance(d, int),
                "DatetimeTrigger 'days' entry must be an int, "
                "got: %s" % type(d).__name__)
            self.check((1 <= d <= 31) or (-31 <= d <= -1),
                "DatetimeTrigger 'days' entry must be in [1, 31] "
                "or [-31, -1], got: %d" % d)

        # weekdays: must be valid Weekday enum values (already converted)
        for w in self.weekdays:
            self.check(isinstance(w, Weekday),
                "DatetimeTrigger 'weekdays' entry must be a Weekday "
                "enum, got: %s" % type(w).__name__)

        # hours: each must be int in 0-23
        for h in self.hours:
            self.check(isinstance(h, int),
                "DatetimeTrigger 'hours' entry must be an int, "
                "got: %s" % type(h).__name__)
            self.check(0 <= h <= 23,
                "DatetimeTrigger 'hours' entry must be in [0, 23], "
                "got: %d" % h)

        # minutes: each must be int in 0-59
        for m in self.minutes:
            self.check(isinstance(m, int),
                "DatetimeTrigger 'minutes' entry must be an int, "
                "got: %s" % type(m).__name__)
            self.check(0 <= m <= 59,
                "DatetimeTrigger 'minutes' entry must be in "
                "[0, 59], got: %d" % m)

    def _day_matches(self, dt_date, days_list):
        """Checks if a date's day-of-month matches any value in ``days_list``.

        Positive values match directly. Negative values count backwards from
        the end of the month (e.g. -1 = last day).

        Args:
            dt_date: A ``date`` or ``datetime`` object.
            days_list: A list of int day values (positive or negative).

        Returns:
            True if the date's day matches any value in the list.
        """
        last_day = get_last_day_of_month(dt_date).day
        for d in days_list:
            if d < 0:
                # Negative: count from end of month.
                # -1 = last_day, -2 = last_day - 1, etc.
                effective_day = last_day + d + 1
                if effective_day == dt_date.day:
                    return True
            else:
                if d == dt_date.day:
                    return True
        return False

    def matches(self, dt):
        """Returns True if the given datetime satisfies all trigger conditions.

        Each non-empty field must be satisfied (AND). Within a field, any
        matching value is sufficient (OR). Empty fields are wildcards.

        Args:
            dt: A ``datetime`` object to check against.

        Returns:
            True if the datetime matches all trigger constraints.
        """
        # YEAR CHECK
        if self.years and dt.year not in self.years:
            return False

        # MONTH CHECK
        if self.months:
            month_values = [m.value for m in self.months]
            if dt.month not in month_values:
                return False

        # DAY CHECK
        if self.days:
            if not self._day_matches(dt, self.days):
                return False

        # WEEKDAY CHECK
        if self.weekdays:
            current_wd = get_weekday(dt)
            if current_wd not in self.weekdays:
                return False

        # HOUR CHECK
        if self.hours and dt.hour not in self.hours:
            return False

        # MINUTE CHECK
        if self.minutes and dt.minute not in self.minutes:
            return False

        return True

    def matches_range(self, dt_start, dt_end):
        """Returns True if ANY datetime within ``[dt_start, dt_end)`` satisfies
        the trigger conditions.

        Uses a Scoped Candidate Generation approach: iterates at day
        granularity through the range, applying year/month/day/weekday filters
        first. Only when a candidate day passes all day-level filters are the
        finer hour/minute constraints checked.

        Args:
            dt_start: Start of range (inclusive), a ``datetime`` object.
            dt_end:   End of range (exclusive), a ``datetime`` object.

        Returns:
            True if any datetime in the range matches the trigger.
        """
        self.check(dt_start < dt_end, "start must be before end")

        # Convert trigger lists to sets for O(1) membership tests
        year_set = set(self.years) if self.years else None
        month_set = (set(m.value for m in self.months)
                     if self.months else None)
        weekday_set = set(self.weekdays) if self.weekdays else None
        hour_set = set(self.hours) if self.hours else None
        minute_set = set(self.minutes) if self.minutes else None

        # Iterate day-by-day through the range
        current_day = dt_start.date()
        end_day = dt_end.date()

        while current_day <= end_day:
            # --- Day-Level Filters (fast rejection) ---

            # YEAR CHECK
            if year_set is not None and current_day.year not in year_set:
                current_day += timedelta(days=1)
                continue

            # MONTH CHECK (with skip-to-next-month optimization)
            if month_set is not None and \
               current_day.month not in month_set:
                # Skip to first day of next month
                if current_day.month == 12:
                    current_day = date(current_day.year + 1, 1, 1)
                else:
                    current_day = date(current_day.year,
                                       current_day.month + 1, 1)
                continue

            # DAY-OF-MONTH CHECK
            if self.days:
                if not self._day_matches(current_day, self.days):
                    current_day += timedelta(days=1)
                    continue

            # WEEKDAY CHECK
            if weekday_set is not None:
                wd = get_weekday(
                    datetime(current_day.year, current_day.month,
                             current_day.day)
                )
                if wd not in weekday_set:
                    current_day += timedelta(days=1)
                    continue

            # --- Hour/Minute Filters ---

            # Determine the valid time window for this particular
            # day, accounting for the range boundaries.
            time_start = time(0, 0)
            time_end = time(23, 59, 59)

            if current_day == dt_start.date():
                time_start = dt_start.time()
            if current_day == dt_end.date():
                time_end = dt_end.time()
                # If dt_end time is midnight (00:00), this day is
                # excluded entirely from the half-open range.
                if dt_end.time() == time(0, 0):
                    current_day += timedelta(days=1)
                    continue

            # If both hours and minutes are wildcarded, any time on
            # this day matches — return True immediately.
            if not self.hours and not self.minutes:
                return True

            # Determine candidate hours and minutes
            candidate_hours = (self.hours
                               if self.hours else range(0, 24))
            candidate_minutes = (self.minutes
                                 if self.minutes else range(0, 60))

            # Check if any (hour, minute) combo falls in the window
            for h in candidate_hours:
                for m in candidate_minutes:
                    candidate_time = time(h, m)
                    if time_start <= candidate_time <= time_end:
                        return True

            current_day += timedelta(days=1)

        return False

    def to_json(self):
        """Converts the trigger to a JSON-serializable dictionary.

        Month and Weekday enum values are serialized as their integer values.
        """
        result = {}
        for f in self.fields:
            val = getattr(self, f.name)
            if f.name == "months":
                result[f.name] = [m.value for m in val]
            elif f.name == "weekdays":
                result[f.name] = [w.value for w in val]
            else:
                result[f.name] = list(val)
        return result


def get_weekday(dt):
    """Returns the weekday, as an enum."""
    value = int((dt.weekday() + 1) % 7)
    return Weekday(value)

def get_weekday_str(dt):
    """Returns the weekday, as a string."""
    # Use `title()` to uppercase only the first letter
    return get_weekday(dt).name.title()

def get_days_until_weekday(dt, weekday):
   """Takes in a weekday and returns the number of days 'dt' is away from the
   specified weekday. This looks FORWARDS for the next occurrence of the
   weekday.
   Returns 0 if the current weekday matches.
   """
   wd1 = get_weekday(dt).value
   wd2 = weekday.value

   if wd1 > wd2:
       return 7 + (wd2 - wd1)
   elif wd1 < wd2:
       return wd2 - wd1
   else:
       return 0

def get_days_since_weekday(dt, weekday):
    """Takes in a weekday and returns the number of days 'dt' is away from the
    weekday. This looks BACKWARDS for the most recent occurrence of the weekday.
    Returns 0 if the current weekday matches.
    """
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

def is_weekend(dt):
    """Returns True if the given day is a Saturday or Sunday."""
    return dt.weekday() in [5, 6]

def is_weekday(dt):
    """Returns True if the given day is Monday-Friday."""
    return dt.weekday() not in [5, 6]

def is_morning(dt):
    """Returns True if the given time in the early morning before noon."""
    return dt.hour >= 6 and dt.hour < 12

def is_afternoon(dt):
    """Returns True if the given time is between noon and the evening."""
    return dt.hour >= 12 and dt.hour < 17

def is_evening(dt):
    """Returns True if the given time is between evening and night."""
    return dt.hour >= 17 and dt.hour < 21

def is_night(dt):
    """Returns True if the given time is between night time and early morning."""
    return dt.hour >= 21 or dt.hour < 6

def is_exact_midnight(dt):
    """Returns True if the given time is exactly at midnight (hour 0, minute 0,
    second 0).
    """
    return dt.hour == 0 and dt.minute == 0 and dt.second == 0

def is_workhours(dt):
    """Returns True if the time is between 9am and 5pm."""
    return dt.hour >= 9 and dt.hour < 17

def is_spring(dt):
    """Returns True if the current season is spring. (Give or take a few days.)"""
    spring_start = datetime(dt.year, 3, 20)
    spring_end = datetime(dt.year, 6, 20)
    return dt.timestamp() >= spring_start.timestamp() and \
           dt.timestamp() < spring_end.timestamp()

def is_summer(dt):
    """Returns True if the current season is summer. (Give or take a few days.)"""
    summer_start = datetime(dt.year, 6, 20)
    summer_end = datetime(dt.year, 9, 20)
    return dt.timestamp() >= summer_start.timestamp() and \
           dt.timestamp() < summer_end.timestamp()

def is_fall(dt):
    """Returns True if the current season is fall. (Give or take a few days.)"""
    fall_start = datetime(dt.year, 9, 20)
    fall_end = datetime(dt.year, 12, 20)
    return dt.timestamp() >= fall_start.timestamp() and \
           dt.timestamp() < fall_end.timestamp()

def is_winter(dt):
    """Returns True if the current season is winter. (Give or take a few days.)"""
    winter_start = datetime(dt.year, 12, 20) if dt.month > 3 else datetime(dt.year, 1, 1)
    winter_end = datetime(dt.year, 12, 31) if dt.month > 3 else datetime(dt.year, 3, 20)
    return dt.timestamp() >= winter_start.timestamp() and \
           dt.timestamp() < winter_start.timestamp()

def get_thanksgiving_day(year: int):
    """Given a specific year, this returns a datetime object indicating the date of
    Thanksgiving this year.
    (Thanksgiving falls on the fourth Thursday of November in the USA.)
    """
    dt = datetime(year, 11, 30) # start at the end of november
    dt_weekday = get_weekday(dt)

    # if the last day of November is a Thursday, return it
    if dt_weekday == Weekday.THURSDAY:
        return dt

    # otherwise, we need to backtrack to the previous Thursday
    days_back = (dt_weekday.value - Weekday.THURSDAY.value)

    if days_back > 0:
        return add_days(dt, -days_back)
    return add_days(dt, -(7 + days_back))

def get_last_day_of_month(dt):
    """Given a datetime, this returns the datetime representing the final day of
    that datetime's month.
    Special thanks to this clever solution:
      https://stackoverflow.com/questions/42950/get-the-last-day-of-the-month
    """
    next_month = dt.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)

def set_time_beginning_of_day(dt):
    """Creates and returns a copy of the given datetime with the time set to
    12:00am.
    """
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def set_time_end_of_day(dt):
    """Creates and returns a copy of the given datetime with the time set to
    11:59pm.
    """
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)

def add_seconds(dt, mult=1):
    """Adds 'mult' number of seconds to the given datetime."""
    return dt.fromtimestamp(dt.timestamp() + mult)

def add_minutes(dt, mult=1):
    """Adds 'mult' numbers of minutes to the given datetime."""
    return dt.fromtimestamp(dt.timestamp() + (mult * 60))

def add_hours(dt, mult=1):
    """Adds 'mult' numbers of hours to the given datetime."""
    return dt.fromtimestamp(dt.timestamp() + (mult * 3600))

def add_days(dt, mult=1):
    """Adds 'mult' numbers of days to the given datetime."""
    return dt.fromtimestamp(dt.timestamp() + (mult * 86400))

def add_weeks(dt, mult=1):
    """Adds 'mult' numbers of weeks to the given datetime."""
    return dt.fromtimestamp(dt.timestamp() + (mult * 604800))

def diff_in_seconds(dt1, dt2):
    """Returns the difference between the two datetimes in seconds."""
    # make copies of the datetimes, with both timezones removed, so we can do a
    # clean subtraction
    notz_dt1 = dt1.replace(tzinfo=None)
    notz_dt2 = dt2.replace(tzinfo=None)
    diff = notz_dt1 - notz_dt2
    return diff.total_seconds()

def diff_in_minutes(dt1, dt2):
    """Returns the difference between the two datetimes in minutes."""
    return diff_in_seconds(dt1, dt2) / 60

def diff_in_hours(dt1, dt2):
    """Returns the difference between the two datetimes in hours."""
    return diff_in_seconds(dt1, dt2) / 3600

def diff_in_days(dt1, dt2):
    """Returns the difference between the two datetimes in days."""
    return diff_in_seconds(dt1, dt2) / 86400

def diff_in_weeks(dt1, dt2):
    """Returns the difference between the two datetimes in weeks."""
    return diff_in_seconds(dt1, dt2) / 604800

def diff_in_seconds_minutes(dt1, dt2):
    """Returns two numbers in an array:

      [remaining_diff_in_seconds, diff_in_minutes]
    """
    mins = int(diff_in_minutes(dt1, dt2))
    secs = diff_in_seconds(dt1, dt2)

    # if there was at least one minute of difference, subtract out the amount
    # from the total seconds diff to get the remaining seconds diff
    if mins > 0:
        secs -= mins * 60
    return [secs, mins]

def diff_in_seconds_minutes_hours(dt1, dt2):
    """Returns three numbers in an array:

      [
          remaining_diff_in_seconds,
          remaining_diff_in_minutes,
          diff_in_hours
      ]
    """
    hours = int(diff_in_hours(dt1, dt2))
    [secs, mins] = diff_in_seconds_minutes(dt1, dt2)

    # if there was at least one hour's worth of time difference, subtract out
    # those minutes from the total number of minutes
    if hours > 0:
        mins -= hours * 60

    return [secs, mins, hours]

def split_by_day(dt_start, dt_end):
    """Takes in two datetimes and returns a list of datetimes, split by day within
    the given range.
    """
    # ensure the start is before the end
    assert dt_start.timestamp() <= dt_end.timestamp(), \
        "The starting datetime must be before the ending datetime"

    # create a list of datetimes, one for each day in the range
    days = []
    current_day = set_time_beginning_of_day(dt_start)
    last_day = set_time_beginning_of_day(dt_end)
    last_day_timestamp = last_day.timestamp()
    while current_day.timestamp() <= last_day_timestamp:
        days.append(current_day)
        current_day = add_days(current_day, 1)
    return days

def has_same_year(dt1, dt2):
    """Returns True if the two datetimes share the same year."""
    return dt1.year == dt2.year

def has_same_month(dt1, dt2):
    """Returns True if the two datetimes share the same month."""
    return dt1.month == dt2.month

def has_same_day(dt1, dt2):
    """Returns True if the two datetimes share the same day."""
    return dt1.day == dt2.day

def has_same_exact_day(dt1, dt2):
    """Returns True if the two datetimes are on the same calendar month."""
    return has_same_year(dt1, dt2) and \
           has_same_month(dt1, dt2)

def has_same_exact_day(dt1, dt2):
    """Returns True if the two datetimes are on the same calendar day."""
    return has_same_year(dt1, dt2) and \
           has_same_month(dt1, dt2) and \
           has_same_day(dt1, dt2)

def has_same_year_month(dt1, dt2):
    """Returns True if the two datetimes share the same year AND month."""
    return dt1.year == dt2.year and \
           dt1.month == dt2.month

def has_same_year_month_day(dt1, dt2):
    """Returns True if the two datetimes share the same year, month, AND day."""
    return dt1.year == dt2.year and \
           dt1.month == dt2.month and \
           dt1.day == dt2.day

def format_yyyymmdd(dt):
    """Returns a string with the "YYYY-MM-DD" format."""
    return dt.strftime("%Y-%m-%d")

def format_yyyymmdd_hhmmss_24h(dt):
    """Returns a string with the "YYYY-MM-DD HH:MM:SS" (24 hour) format."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_yyyymmdd_hhmmss_12h(dt):
    """Returns a string with the "YYYY-MM-DD HH:MM:SS AM/PM" (12 hour) format."""
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")

def parse_yyyymmdd(text: str):
    """Parses a YYYY-MM-DD string and returns the year, month, and day, in an array
    of three integers [year, month, day]. Returns None if parsing failed.
    """
    # attempt parsing with multiple delimeters
    delimeters = ["-", "/", "."]
    for delim in delimeters:
        try:
            d = datetime.strptime(text, "%Y" + delim + "%m" + delim + "%d")
            return [d.year, d.month, d.day]
        except:
            pass
    return None

def parse_weekday(text: str):
    """Returns a weekday enum value on the given text. Returns None if the string
    isn't recognized.
    """
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

def parse_time_offset(text: str):
    """Parses a suffixed time offset string (ex: "1w", "2d", "3h", "4m"). Returns 0
    if nothing is recognized.
    """
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

def parse_time_clock(text: str):
    """Attempts to parse timestamps such as "9pm" or "10:30am".

    Returns an (hour, minute) tuple.
    """
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

def parse_datetime(args: list, now=None):
    """Parses a single datetime from a variety of formats. Returns a datetime
    object, or None, depending on what was found in the given string arguments.

    If `now` is provided (a datetime object), the parsed datetime will use `now`
    as a reference point from which to jump. (By default, `now` becomes
    `datetime.now()`.) For example, if the args `["1h" "20m"]` were provided, the
    resulting datetime object would be one hour and twenty minutes after `now`.
    """
    def p_weekday(text: str):
        """Returns a weekday number based on the given text. Returns None if the
        string isn't recognized.
        """
        result = parse_weekday(text)
        return None if result is None else result.value + 1

    def g_weekday(dt: datetime):
        """Converts Python's monday-first weekday encoding to my sunday-first
        encoding.
        """
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

