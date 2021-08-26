import os
import shutil
from mock import patch

import common

import pytest


def test_check_ip():
    assert common.check_ip("1.2.3.4")


@pytest.mark.parametrize(
    "input,expected",
    [
        ("my-host", "my-host"),
        ("with-slash/0", "with-slash%2f0"),
        ("foo*bar", "foo%2abar"),
        ("with-%", "with-%25"),
    ],
)
def test_sanitize_nagios_name(input, expected):
    assert common.sanitize_nagios_name(input) == expected


class TestInitializeInprogressConfigFileCleanup:

    """Test that we remove the appropriate config files"""

    @patch("common.relation_get")
    def test_with_no_related_unit(self, rget_mock, tmpdir):
        """No related unit case.

        If there's no related unit (e.g. called via another hook like e.g.
        config-changed), only the old monolithic charm config file should be removed.
        """
        with patch("common.OLD_CHARM_CFG", "{}/charm.cfg".format(tmpdir)) as _1, \
             patch("common.HOST_TEMPLATE", "{}/juju-host_{{}}.cfg".format(tmpdir)) as _2, \
             patch("common.HOSTGROUP_TEMPLATE", "{}/juju-hostgroup_{{}}.cfg".format(tmpdir)) as _3:
            existing_files = [
                common.OLD_CHARM_CFG,
                common.HOST_TEMPLATE.format("host-1"),
                common.HOST_TEMPLATE.format("host-2"),
                common.HOST_TEMPLATE.format("monitors:42_7_host-2"),
                common.HOST_TEMPLATE.format("fakesha_host-2"),
                common.HOSTGROUP_TEMPLATE.format("host"),
                common.HOSTGROUP_TEMPLATE.format("monitors:42_7_host"),
                common.HOSTGROUP_TEMPLATE.format("fakesha_host"),
            ]
            expected_files_to_delete = [
                common.OLD_CHARM_CFG,
            ]
            rget_mock.return_value = None
            self._run_test(existing_files, expected_files_to_delete)

    @patch("common.remote_unit")
    @patch("common.relation_id")
    @patch("common.get_model_id_sha")
    @patch("common.relation_get")
    def test_with_related_unit(self, rget_mock, sha_mock, rid_mock, runit_mock, tmpdir):
        """Related unit case.

        If there is a related unit, we should remove:
        * The old monolithic charm.cfg file
        * Host files possibly related to the related unit
        * Likely related hostgroup files
        """
        """Test that related unit files get removed as expected."""
        with patch("common.OLD_CHARM_CFG", "{}/charm.cfg".format(tmpdir)) as _1, \
             patch("common.HOST_TEMPLATE", "{}/juju-host_{{}}.cfg".format(tmpdir)) as _2, \
             patch("common.HOSTGROUP_TEMPLATE", "{}/juju-hostgroup_{{}}.cfg".format(tmpdir)) as _3:
            related_unit_hostname = "host-2"
            existing_files = [
                common.OLD_CHARM_CFG,
                common.HOST_TEMPLATE.format("host-1"),
                common.HOST_TEMPLATE.format("host-2"),
                common.HOST_TEMPLATE.format("monitors:42_7_host-2"),
                common.HOST_TEMPLATE.format("fakesha_host-2"),
                common.HOST_TEMPLATE.format("host-3"),
                common.HOST_TEMPLATE.format("monitors:42_8_host-3"),
                common.HOST_TEMPLATE.format("fakesha_host-3"),
                common.HOSTGROUP_TEMPLATE.format("host"),
                common.HOSTGROUP_TEMPLATE.format("monitors:42_7_host"),
                common.HOSTGROUP_TEMPLATE.format("monitors:42_8_host"),
                common.HOSTGROUP_TEMPLATE.format("fakesha_host"),
            ]
            expected_files_to_delete = [
                common.OLD_CHARM_CFG,
                common.HOST_TEMPLATE.format("host-2"),
                common.HOST_TEMPLATE.format("monitors:42_7_host-2"),
                common.HOST_TEMPLATE.format("fakesha_host-2"),
                common.HOSTGROUP_TEMPLATE.format("host"),
                common.HOSTGROUP_TEMPLATE.format("monitors:42_7_host"),
                common.HOSTGROUP_TEMPLATE.format("fakesha_host"),
            ]
            fake_sha = "fakesha"
            fake_relation_id = "monitors:42"
            fake_remote_unit = "foo/7"

            rget_mock.return_value = {
                common.TARGET_ID_KEY: related_unit_hostname,
                common.MODEL_ID_KEY: "fake",
            }
            sha_mock.return_value = fake_sha
            rid_mock.return_value = fake_relation_id
            runit_mock.return_value = fake_remote_unit
            self._run_test(existing_files, expected_files_to_delete)

    def _run_test(self, existing_files, expected_files_to_delete):
        """Inner test function.  Assumes all mocks have been appropriately set up."""
        self.create_files(existing_files)
        common._initialize_inprogress_config_files()
        assert not any(os.path.exists(file) for file in expected_files_to_delete)
        expected_leftover_files = set(existing_files) - set(expected_files_to_delete)
        assert all(os.path.exists(file) for file in expected_leftover_files)

    def create_files(self, filenames):
        for filename in filenames:
            with open(filename, 'w') as _:
                pass
