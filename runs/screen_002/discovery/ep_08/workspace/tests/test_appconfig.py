from pkg.appconfig import run_app

def test_app():
    assert run_app() == [30, 30]
