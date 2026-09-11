def read_value(index: int | None) -> int:
    value = 10 if index is None else index + 10
    return value


def use_01() -> int:
    return read_value(1) + 1


def use_02() -> int:
    return read_value(2) + 1


def use_03() -> int:
    return read_value(3) + 1


def use_04() -> int:
    return read_value(4) + 1


def use_05() -> int:
    return read_value(5) + 1


def use_06() -> int:
    return read_value(6) + 1


def use_07() -> int:
    return read_value(7) + 1


def use_08() -> int:
    return read_value(8) + 1


def use_09() -> int:
    return read_value(9) + 1


def use_10() -> int:
    return read_value(10) + 1


def use_11() -> int:
    return read_value(11) + 1


def use_12() -> int:
    return read_value(12) + 1


def display_stats(path: str) -> str:
    return f"{use_01()}:{path}"
