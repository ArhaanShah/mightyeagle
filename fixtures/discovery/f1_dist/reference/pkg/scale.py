def get_value() -> int:
    return 2


def use_b() -> int:
    return get_value() + 1


def use_b_again() -> int:
    return get_value() + 2
