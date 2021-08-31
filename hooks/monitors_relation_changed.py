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

import glob
import os
import re
import sys
import time
from collections import defaultdict

from charmhelpers.core.hookenv import (
    DEBUG,
    WARNING,
    ingress_address,
    log,
    related_units,
    relation_get,
    relation_ids,
    status_set,
)

import yaml

from common import (
    HOST_PREFIX_MAX_LENGTH,
    HOST_PREFIX_MIN_LENGTH,
    HOST_TEMPLATE,
    MODEL_ID_KEY,
    TARGET_ID_KEY,
    customize_service,
    flush_inprogress_config,
    get_model_id_sha,
    get_nagios_host_config_path,
    get_pynag_host,
    get_pynag_service,
    initialize_inprogress_config,
    refresh_hostgroups,
)


MACHINE_ID_KEY = "machine_id"
REQUIRED_REL_DATA_KEYS = ["target-address", "monitors", TARGET_ID_KEY]

MINIMUM_HOOK_TIME_IN_SECONDS = 2


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
        relation_data[TARGET_ID_KEY] = unit.replace("/", "-")
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


def main(argv, full_rewrite=False):  # noqa: C901
    # Note that one can pass in args positionally, 'monitors.yaml targetid
    # and target-address' so the hook can be tested without being in a hook
    # context.
    #

    start_time = time.time()

    if len(argv) > 1:
        relation_settings = {"monitors": open(argv[1]).read(), TARGET_ID_KEY: argv[2]}

        if len(argv) > 3:
            relation_settings["target-address"] = argv[3]
        all_relations = {"monitors:99": {"testing/0": relation_settings}}
    else:
        all_relations = _collect_relation_data()

    # Hack to work around http://pad.lv/1025478
    targets_with_addresses = set()

    for relid, units in all_relations.iteritems():
        for unit, relation_settings in units.items():
            if TARGET_ID_KEY in relation_settings:
                targets_with_addresses.add(relation_settings.get(TARGET_ID_KEY))
    new_all_relations = {}

    for relid, units in all_relations.iteritems():
        for unit, relation_settings in units.items():
            if relation_settings.get(TARGET_ID_KEY) in targets_with_addresses:
                if relid not in new_all_relations:
                    new_all_relations[relid] = {}
                new_all_relations[relid][unit] = relation_settings

    all_relations = new_all_relations

    initialize_inprogress_config(full_rewrite=full_rewrite)

    hosts_to_settings = defaultdict(list)
    model_ids = set()
    for units in all_relations.values():
        for relation_settings in units.values():
            target_id = relation_settings.get(TARGET_ID_KEY)
            hosts_to_settings[target_id].append(relation_settings)
            model_id = relation_settings.get(MODEL_ID_KEY)
            if model_id:
                model_ids.add(model_id)

    host_prefixes = compute_host_prefixes(model_ids)

    duplicate_hostnames = set()
    all_hosts = {}
    for target_id, relation_settings_list in hosts_to_settings.items():
        related_model_ids = set(
            [
                relation_settings.get(MODEL_ID_KEY)
                for relation_settings in relation_settings_list
            ]
        )
        deduping_required = len(related_model_ids) > 1
        for relation_settings in relation_settings_list:
            model_id = relation_settings.get(MODEL_ID_KEY)
            if model_id and deduping_required:
                duplicate_hostnames.add(target_id)
                unique_prefix = host_prefixes[model_id]
                relation_settings[TARGET_ID_KEY] = "{}_{}".format(
                    unique_prefix, target_id
                )

            deduped_target_id = relation_settings[TARGET_ID_KEY]
            machine_id = relation_settings.get(MACHINE_ID_KEY)

            if not machine_id or not deduped_target_id:
                continue

            # Backwards compatible hostname from machine id
            if not model_id:
                all_hosts[machine_id] = deduped_target_id
            # New hostname from machine id using model id
            else:
                all_hosts.setdefault(model_id, {})
                all_hosts[model_id][machine_id] = deduped_target_id

    if duplicate_hostnames:
        message = "Duplicate host names detected: {}".format(
            ", ".join(sorted(duplicate_hostnames))
        )
        log(message, level=WARNING)
        status_set("active", message)
    else:
        status_set("active", "ready")

    for relid, units in all_relations.items():
        apply_relation_config(relid, units, all_hosts)

    cleanup_leftover_hosts(all_relations)
    refresh_hostgroups()
    flush_inprogress_config()

    # Reduce the chance of reload races, i.e. if there is a series of hooks and this
    # hook completes before the last hook's nagios reload completes.
    elapsed = time.time() - start_time
    if elapsed < MINIMUM_HOOK_TIME_IN_SECONDS:
        time.sleep(MINIMUM_HOOK_TIME_IN_SECONDS - elapsed)

    os.system("service nagios3 reload")


def cleanup_leftover_hosts(all_relations):
    """Cleanup leftover host files.

    While the charm deletes files potentially related to the immediate unit being added
    or removed, the de-duplication code introduces the possibility that unrelated units
    may end up with leftover files which are not cleaned up since no relevant
    relation-changed hooks fired for that unit, yet the unit's hostname in nagios
    changed as a side effect of a relation-changed hook fired on another unit.

    To accomodate for this, we can compare the set of generated host files present
    in the Nagios config against the set we presently intend to be present, and
    remove the extras.
    """
    expected_paths = set()
    for units in all_relations.itervalues():
        for relation_settings in units.itervalues():
            target_id = relation_settings[TARGET_ID_KEY]
            expected_path = get_nagios_host_config_path(target_id)
            expected_paths.add(expected_path)

    actual_paths = set(glob.glob(HOST_TEMPLATE.format("*")))

    leftovers = actual_paths - expected_paths
    for path in leftovers:
        os.unlink(path)


def compute_host_prefixes(model_ids):
    """Compute short unique identifiers based off of model UUIDs."""
    hashes = {}
    for model_id in model_ids:
        hashes[model_id] = get_model_id_sha(model_id)

    result = {}
    # Try to find a short unique portion of the sha256sums to use.
    # Loop through with longer and longer lengths until we find we have a set of
    # unique IDs.
    for i in range(HOST_PREFIX_MIN_LENGTH, HOST_PREFIX_MAX_LENGTH + 1):
        for model_id in model_ids:
            result[model_id] = hashes[model_id][:i]
        # If we have as many unique model IDs as hash fragments, break out of the loop.
        if len(set(result.values())) == len(model_ids):
            break
    return result


def apply_relation_config(relid, units, all_hosts):  # noqa: C901
    for unit, relation_settings in units.iteritems():
        target_id = relation_settings[TARGET_ID_KEY]
        if os.path.exists(get_nagios_host_config_path(target_id)):
            # Skip updating files unrelated to the hook at hand unless they were
            # deliberately removed with the intent of them being rewritten.
            continue

        monitors = relation_settings["monitors"]
        machine_id = relation_settings.get(MACHINE_ID_KEY)
        parent_host = None

        model_id = relation_settings.get(MODEL_ID_KEY)

        if machine_id:

            container_regex = re.compile(r"(\d+)/lx[cd]/\d+")
            if container_regex.search(machine_id):
                parent_machine = container_regex.search(machine_id).group(1)

                # Get hostname using model id
                if model_id:
                    model_hosts = all_hosts.get(model_id, {})
                    parent_host = model_hosts.get(parent_machine)

                # Get hostname without model id
                # this conserves backwards compatibility with older
                # versions of charm-nrpe that don't provide model_id
                elif parent_machine in all_hosts:
                    parent_host = all_hosts[parent_machine]

        # If not set, we don't mess with it, as multiple services may feed
        # monitors in for a particular address. Generally a primary will set
        # this to its own private-address
        target_address = relation_settings.get("target-address")

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
