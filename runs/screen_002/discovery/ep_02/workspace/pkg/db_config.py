"""Database configuration utilities.

The original implementation declared the ``port`` field of ``DBConfig`` as a
``str`` while the runtime values are integers.  This mismatch caused mypy to
report type errors.  The fix is to type the field as ``int`` – matching the
actual usage – without altering runtime behaviour.
"""

from typing import TypedDict


class DBConfig(TypedDict):
    """TypedDict representing database configuration.

    The ``port`` is an integer – the typical numeric TCP port used to connect
    to the database.
    """

    port: int


def get_db_port(cfg: DBConfig) -> int:
    """Extract the ``port`` from a ``DBConfig``.

    The function simply returns the integer port value.
    """

    return cfg["port"]


def run_db() -> int:
    """Read the port twice and return their sum.

    The expected result (used in the test suite) is ``5432 + 5432 == 10864``.
    """

    cfg: DBConfig = {"port": 5432}
    p1 = get_db_port(cfg)
    p2 = get_db_port(cfg)
    return p1 + p2
