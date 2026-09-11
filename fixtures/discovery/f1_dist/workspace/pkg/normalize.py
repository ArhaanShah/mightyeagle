def get_value() -> int | None:
    return 1


def use_a() -> int:
    return get_value() + 1


def use_a_again() -> int:
    return get_value() + 2
