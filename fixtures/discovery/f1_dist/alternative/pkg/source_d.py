def get_value() -> int | None:
    return 4


def use_d() -> int:
    value = get_value()
    return 1 if value is None else value + 1


def use_d_again() -> int:
    value = get_value()
    return 2 if value is None else value + 2
