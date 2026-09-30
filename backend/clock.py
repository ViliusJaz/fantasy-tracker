"""The current time, in one place, so tests and offline replays can freeze it."""
from datetime import date, datetime


def now():
    """Local time without a time zone (like datetime.now())."""
    return datetime.now()


def today():
    return date.today()
