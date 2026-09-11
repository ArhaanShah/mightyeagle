def get_value() -> int | None:
    return 6


def use_f() -> int:
    return get_value() + 1


def use_f_again() -> int:
    return get_value() + 2
