import datetime
from datetime import datetime as datetime_type


def get_current_datetime_string() -> str:
    """
    Get the current UTC datetime as a string in the format "YYYY-MM-DDTHH:MM:SSZ".
    This function returns the current time in UTC, formatted as a string.
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def datetime_to_datetime_string(input: datetime_type) -> str:
    """
    Convert a datetime object to a string in UTC format.
    If the input datetime does not have timezone information, it will be assumed to be in UTC
    and converted accordingly.
    """
    if input.tzinfo is None:
        input = input.replace(tzinfo=datetime.timezone.utc)
    return input.strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_to_datetime_string(input: float) -> str:
    """
    Convert a timestamp to a datetime string in UTC format.
    """
    dt = datetime.datetime.fromtimestamp(input, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
