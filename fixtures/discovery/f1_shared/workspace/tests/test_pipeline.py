from pkg.pipeline import display_stats, read_value


def test_value_contract():
    assert [read_value(i) for i in (-5, 0, 2, 9)] == [5, 10, 12, 19]
    assert display_stats("path") == "12:path"
