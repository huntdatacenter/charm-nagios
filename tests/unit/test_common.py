import common

import pytest


def test_check_ip():
    assert common.check_ip("1.2.3.4")


@pytest.mark.parametrize(
    "input,expected",
    [
        ("my-host", "my-host"),
        ("with-slash/0", "with-slash%2f0"),
        ("foo/../../bar", "foo%2f%2e%2e%2f%2e%2e%2fbar"),
        ("with-%", "with-%25"),
    ],
)
def test_sanitize_nagios_name(input, expected):
    assert common.sanitize_nagios_name(input) == expected
