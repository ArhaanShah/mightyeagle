def get_value() -> int | None:
    return 3


def use_c() -> int:
    return get_value() + 1


def use_c_again() -> int:
    return get_value() + 2
