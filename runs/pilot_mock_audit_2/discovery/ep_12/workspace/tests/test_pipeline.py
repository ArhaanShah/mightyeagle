from pkg.pipeline import load_values, compute_summary, format_result, display_stats, display_secondary_stats, display_tertiary_stats

def test_load_values_returns_list():
    result = load_values("any_path")
    assert isinstance(result, list)
    assert len(result) > 0

def test_compute_summary_empty():
    assert compute_summary([]) == 0.0

def test_compute_summary_values():
    result = compute_summary([2.0, 4.0])
    assert result == 3.0

def test_format_result():
    assert format_result(3.14) == "Mean: 3.14"

def test_display_stats_returns_string():
    r = display_stats("path")
    assert isinstance(r, str)

def test_display_secondary_stats_returns_string():
    r = display_secondary_stats("path")
    assert isinstance(r, str)

def test_display_tertiary_stats_returns_string():
    r = display_tertiary_stats("path")
    assert isinstance(r, str)
