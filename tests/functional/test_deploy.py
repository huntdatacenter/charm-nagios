import pytest
import requests

pytestmark = pytest.mark.asyncio


#########
# TESTS #
#########
async def test_status(deploy_app):
    """Check that the app is in active state."""
    assert deploy_app.status == "active"


async def test_web_interface_is_protected(auth, unit):
    """Check the nagios http interface."""
    host_url = "http://%s/nagios3/" % unit.u.public_address
    r = requests.get(host_url)
    assert r.status_code == 401, "Web Interface is open to the world"

    r = requests.get(host_url, auth=auth)
    assert r.status_code == 200, "Web Admin login failed"


async def test_hosts_being_monitored(auth, unit):
    host_url = (
        "http://%s/cgi-bin/nagios3/status.cgi?hostgroup=all&style=hostdetail"
    ) % unit.u.public_address
    r = requests.get(host_url, auth=auth)
    assert "mysql" in r.text, "Nagios is not monitoring the hosts it supposed to."


async def test_nrpe_monitors_config(relatives, unit, file_contents):
    # look for disk root check in nrpe config
    mysql_unit = relatives["mysql"]["app"].units[0]
    contents = await file_contents("/etc/nagios/nrpe.d/check_disk_root.cfg", mysql_unit)
    assert contents, "disk root check config not found."
