import mock

import pytest

import website_relation_joined


@mock.patch("common.get_local_ingress_address")
@mock.patch("website_relation_joined.config")
@mock.patch("website_relation_joined.relation_set")
@pytest.mark.parametrize(
    "ssl",
    [("only", 443), ("on", 80), ("off", 80)],
    ids=["ssl=only", "ssl=on", "ssl=off"],
)
def test_main(relation_set, config, get_local_ingress_address, ssl):
    get_local_ingress_address.return_value = "example.com"
    config.return_value = {"ssl": ssl[0]}
    website_relation_joined.main()
    relation_set.assert_called_with(None, port=ssl[1], hostname="example.com")
