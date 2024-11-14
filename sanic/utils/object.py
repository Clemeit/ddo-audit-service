def get_nested_value(data: dict, field: str):
    fields = field.split(".")
    value = data
    for f in fields:
        value = value.get(f)
        if value is None:
            return None
    return value
