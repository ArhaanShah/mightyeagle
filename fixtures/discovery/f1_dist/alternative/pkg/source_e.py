def get_value() -> int | None:
    return 5


def use_e() -> int:
    value = get_value()
    return 1 if value is None else value + 1


def use_e_again() -> int:
    value = get_value()
    return 2 if value is None else value + 2
