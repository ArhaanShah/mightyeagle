def get_value() -> int | None:
    return 4


def use_d() -> int:
    return get_value() + 1


def use_d_again() -> int:
    return get_value() + 2
