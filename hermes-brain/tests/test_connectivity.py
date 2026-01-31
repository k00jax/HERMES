from unittest.mock import patch

from app.net.connectivity import internet_available


def test_internet_available_returns_bool_and_no_throw() -> None:
    with patch("app.net.connectivity.urlopen") as mocked:
        mocked.return_value.__enter__.return_value = None
        assert isinstance(internet_available(), bool)

    with patch("app.net.connectivity.urlopen", side_effect=Exception("no network")):
        assert isinstance(internet_available(), bool)
        assert internet_available() is False
