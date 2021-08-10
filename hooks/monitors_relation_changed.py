#!/usr/bin/python
# monitors-relation-changed - Process monitors.yaml into remote nagios monitors
# Copyright Canonical 2012 Canonical Ltd. All Rights Reserved
# Author: Clint Byrum <clint.byrum@canonical.com>
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
import re
import sys
from collections import defaultdict

from charmhelpers.core.hookenv import (
    DEBUG,
    ingress_address,
    log,
    related_units,
    relation_get,
    relation_ids,
)

from common import (
    customize_service,
    flush_inprogress_config,
    get_pynag_host,
    get_pynag_service,
    initialize_inprogress_config,
    refresh_hostgroups,
)

import yaml

REQUIRED_REL_DATA_KEYS = ["target-address", "monitors", "target-id"]
MODEL_ID_KEY = "model_id"


def _prepare_relation_data(unit, rid):
    relation_data = relation_get(unit=unit, rid=rid)

    if not relation_data:
        msg = "no relation data found for unit {} in relation {} - skipping".format(
            unit, rid
        )
        log(msg, level=DEBUG)

        return {}

    if rid.split(":")[0] == "nagios":
        # Fake it for the more generic 'nagios' relation
        relation_data["target-id"] = unit.replace("/", "-")
        relation_data["monitors"] = {"monitors": {"remote": {}}}

    if not relation_data.get("target-address"):
        relation_data["target-address"] = ingress_address(unit=unit, rid=rid)

    for key in REQUIRED_REL_DATA_KEYS:
        if not relation_data.get(key):
            # Note: it seems that some applications don't provide monitors over
            # the relation at first (e.g. gnocchi). After a few hook runs,
            # though, they add the key. For this reason I think using a logging
            # level higher than DEBUG could be misleading
            msg = "{} not found for unit {} in relation {} - skipping".format(
                key, unit, rid
            )
            log(msg, level=DEBUG)

            return {}

    return relation_data


def _collect_relation_data():
    all_relations = defaultdict(dict)

    for relname in ["nagios", "monitors"]:
        for relid in relation_ids(relname):
            for unit in related_units(relid):
                relation_data = _prepare_relation_data(unit=unit, rid=relid)

                if relation_data:
                    all_relations[relid][unit] = relation_data

    return all_relations


def main(argv):  # noqa: C901
    # Note that one can pass in args positionally, 'monitors.yaml targetid
    # and target-address' so the hook can be tested without being in a hook
    # context.
    #

    if len(argv) > 1:
        relation_settings = {"monitors": open(argv[1]).read(), "target-id": argv[2]}

        if len(argv) > 3:
            relation_settings["target-address"] = argv[3]
        all_relations = {"monitors:99": {"testing/0": relation_settings}}
    else:
        all_relations = _collect_relation_data()

    # Hack to work around http://pad.lv/1025478
    targets_with_addresses = set()

    for relid, units in all_relations.iteritems():
        for unit, relation_settings in units.items():
            if "target-id" in relation_settings:
                targets_with_addresses.add(relation_settings["target-id"])
    new_all_relations = {}

    for relid, units in all_relations.iteritems():
        for unit, relation_settings in units.items():
            if relation_settings["target-id"] in targets_with_addresses:
                if relid not in new_all_relations:
                    new_all_relations[relid] = {}
                new_all_relations[relid][unit] = relation_settings

    all_relations = new_all_relations

    initialize_inprogress_config()

    # make a dict of machine ids to target-id hostnames
    all_hosts = {}
    for relid, units in all_relations.items():
        for unit, relation_settings in units.iteritems():
            machine_id = relation_settings.get("machine_id", None)
            model_id = relation_settings.get(MODEL_ID_KEY, None)

            if not machine_id:
                continue

            # Backwards compatable host name from machine id
            if not model_id:
                all_hosts[machine_id] = relation_settings["target-id"]
            # New host name from machine id using model id
            else:
                model_hosts = all_hosts.get(model_id, {})
                model_hosts[machine_id] = relation_settings["target-id"]
                all_hosts[model_id] = model_hosts

    for relid, units in all_relations.items():
        apply_relation_config(relid, units, all_hosts)

    refresh_hostgroups()
    flush_inprogress_config()
    os.system("service nagios3 reload")


def apply_relation_config(relid, units, all_hosts):  # noqa: C901
    for unit, relation_settings in units.iteritems():
        monitors = relation_settings["monitors"]
        target_id = relation_settings["target-id"]
        machine_id = relation_settings.get("machine_id", None)
        parent_host = None
        model_id = None

        if MODEL_ID_KEY in relation_settings.keys():
            model_id = relation_settings[MODEL_ID_KEY]

        if machine_id:

            container_regex = re.compile(r"(\d+)/lx[cd]/\d+")
            if container_regex.search(machine_id):
                parent_machine = container_regex.search(machine_id).group(1)

                # Get host names using model ids
                if model_id:
                    if parent_machine in all_hosts[model_id]:
                        parent_host = all_hosts[model_id][parent_machine]

                # Get host names without model ids
                elif parent_machine in all_hosts:
                    parent_host = all_hosts[parent_machine]

        # If not set, we don't mess with it, as multiple services may feed
        # monitors in for a particular address. Generally a primary will set
        # this to its own private-address
        target_address = relation_settings.get("target-address", None)

        if type(monitors) != dict:
            monitors = yaml.safe_load(monitors)

        # Output nagios config
        host = get_pynag_host(target_id)

        if not target_address:
            raise Exception("No Target Address provied by NRPE service!")
        host.set_attribute("address", target_address)

        if parent_host:
            # We assume that we only want one parent and will overwrite any
            # existing parents for this host.
            host.set_attribute("parents", parent_host)
        host.save()

        for mon_family, mons in monitors["monitors"]["remote"].iteritems():
            for mon_name, mon in mons.iteritems():
                service_name = "%s-%s" % (target_id, mon_name)
                service = get_pynag_service(target_id, service_name)
                try:
                    check_attempts = int(mon.get("max_check_attempts"))
                    service.set_attribute("max_check_attempts", check_attempts)
                except AttributeError:  # mon is a string
                    pass
                except TypeError:  # max_check_attempts is None
                    pass
                except ValueError:  # max_check_attempts is 'null'
                    pass

                if customize_service(service, mon_family, mon_name, mon):
                    service.save()
                else:
                    print(
                        "Ignoring %s due to unknown family %s" % (mon_name, mon_family)
                    )


if __name__ == "__main__":
    main(sys.argv)
