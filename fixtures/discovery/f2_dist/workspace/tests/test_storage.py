def test_fixture_has_independent_modules():
    from pkg.handler_c import Handler as GammaHandler
    from pkg.handler_d import Handler as DeltaHandler
    from pkg.handler_e import Handler as EpsilonHandler
    from pkg.handler_f import Handler as ZetaHandler

    assert GammaHandler().render("x") == "C:x"
    assert DeltaHandler().render("x") == "D:x"
    assert EpsilonHandler().render("x") == "E:x"
    assert ZetaHandler().render("x") == "F:x"
