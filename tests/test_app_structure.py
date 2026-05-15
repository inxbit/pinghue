from pinghue.app import PinghueTextualApp


def test_tui_app_class_is_module_scoped() -> None:
    assert PinghueTextualApp.__module__ == "pinghue.app"
