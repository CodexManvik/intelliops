def test_environment_is_wired():
    """The test runner and package imports work."""
    import common

    assert common.__doc__ is not None
