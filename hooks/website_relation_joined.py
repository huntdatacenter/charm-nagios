#!/usr/bin/python3
# website-relation-joined - Set the hostname into remote nagios http consumers
# Copyright Canonical 2017 Canonical Ltd. All Rights Reserved
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

from charmhelpers.core.hookenv import config, log, relation_set

import common


def main():
    relation_data = {"hostname": common.get_local_ingress_address()}
    sslcfg = config()["ssl"]

    if sslcfg == "only":
        relation_data["port"] = 443
    else:
        relation_data["port"] = 80
    log("website-relation-joined data %s" % relation_data)
    relation_set(None, **relation_data)


if __name__ == "__main__":  # pragma: no cover
    main()
