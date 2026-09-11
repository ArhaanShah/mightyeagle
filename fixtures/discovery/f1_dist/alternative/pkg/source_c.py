def get_value() -> int | None:
    return 3


def use_c() -> int:
    value = get_value()
    return 1 if value is None else value + 1


def use_c_again() -> int:
    value = get_value()
    return 2 if value is None else value + 2
