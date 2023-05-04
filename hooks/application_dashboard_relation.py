#!/usr/bin/env python3

# Copyright Canonical 2021 Canonical Ltd. All Rights Reserved
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import os
import sys

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    is_leader,
    log,
    relation_ids,
    relation_set,
    unit_public_ip,
)

hooks = Hooks()


@hooks.hook("config-changed")
@hooks.hook("application-dashboard-relation-joined")
@hooks.hook("application-dashboard-relation-changed")
def application_dashboard_relation_changed(relation_id=None, remote_unit=None):
    """Register Nagios URL in dashboard charm such as Homer."""
    if not is_leader():
        return
    relations = relation_ids("application-dashboard")
    if not relations:
        return
    tls_configured = config("ssl_key")
    scheme = "https://" if tls_configured else "http://"
    url = scheme + unit_public_ip()
    if config("site_name"):
        subtitle = "[{}] Monitoring and alerting".format(config("site_name"))
        group = "[{}] LMA".format(config("site_name"))
    else:
        subtitle = "Monitoring and alerting"
        group = "LMA"
    icon_file = os.environ.get("JUJU_CHARM_DIR", "") + "/icon.svg"
    icon_data = None
    if os.path.exists(icon_file):
        with open(icon_file) as f:
            icon_data = f.read()
    for rid in relations:
        relation_set(
            rid,
            app=True,
            relation_settings={
                "name": "Nagios",
                "url": url,
                "subtitle": subtitle,
                "icon": icon_data,
                "group": group,
            },
        )


if __name__ == "__main__":
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))
