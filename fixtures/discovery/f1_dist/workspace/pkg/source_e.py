def get_value() -> int | None:
    return 5


def use_e() -> int:
    return get_value() + 1


def use_e_again() -> int:
    return get_value() + 2
