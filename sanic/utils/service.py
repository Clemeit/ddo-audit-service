from datetime import date, datetime


def datetime_to_json_formatting(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
