import glob
import hashlib
import os
import os.path
import re
import shutil
import socket
import subprocess
import tempfile

from charmhelpers.core.hookenv import (
    config,
    log,
    network_get,
    network_get_primary_address,
    relation_get,
    relation_id,
    remote_unit,
    unit_get,
)

from pynag import Model

INPROGRESS_DIR = "/etc/nagios3-inprogress"
INPROGRESS_CFG = "/etc/nagios3-inprogress/nagios.cfg"
INPROGRESS_CONF_D = "/etc/nagios3-inprogress/conf.d"
OLD_CHARM_CFG = "/etc/nagios3-inprogress/conf.d/charm.cfg"
HOST_TEMPLATE = "/etc/nagios3-inprogress/conf.d/juju-host_{}.cfg"
HOSTGROUP_TEMPLATE = "/etc/nagios3-inprogress/conf.d/juju-hostgroup_{}.cfg"
MAIN_NAGIOS_BAK = "/etc/nagios3.bak"
MAIN_NAGIOS_DIR = "/etc/nagios3"
MAIN_NAGIOS_CFG = "/etc/nagios3/nagios.cfg"
PLUGIN_PATH = "/usr/lib/nagios/plugins"

MODEL_ID_KEY = "model_id"
TARGET_ID_KEY = "target-id"

HOST_PREFIX_MIN_LENGTH = 7
HOST_PREFIX_MAX_LENGTH = 64  # max length of sha256sum in hex

SANITIZE_ESCAPE_CHAR = "%"
SANITIZE_CHARS = [
    SANITIZE_ESCAPE_CHAR,  # Must be first
    "/",
    "*",
]

Model.cfg_file = INPROGRESS_CFG
Model.pynag_directory = INPROGRESS_CONF_D

REDUCE_RE = re.compile(r"[\W_]")


def check_ip(n):
    try:
        socket.inet_pton(socket.AF_INET, n)

        return True
    except socket.error:
        try:
            socket.inet_pton(socket.AF_INET6, n)

            return True
        except socket.error:
            return False


def get_local_ingress_address(binding="website"):
    # using network-get to retrieve the address details if available.
    log("Getting hostname for binding %s" % binding)
    try:
        network_info = network_get(binding)

        if network_info is not None and "ingress-addresses" in network_info:
            log("Using ingress-addresses")
            hostname = network_info["ingress-addresses"][0]
            log(hostname)

            return hostname
    except NotImplementedError:
        # We'll fallthrough to the Pre 2.3 code below.
        pass

    # Pre 2.3 output
    try:
        hostname = network_get_primary_address(binding)
        log("Using primary-addresses")
    except NotImplementedError:
        # pre Juju 2.0
        hostname = unit_get("private-address")
        log("Using unit_get private address")
    log(hostname)

    return hostname


def get_remote_relation_attr(remote_unit, attr_name, relation_id=None):
    args = ["relation-get", attr_name, remote_unit]

    if relation_id is not None:
        args.extend(["-r", relation_id])

    return subprocess.check_output(args).strip()


def get_ip_and_hostname(remote_unit, relation_id=None):
    hostname = get_remote_relation_attr(remote_unit, "ingress-address", relation_id)

    if hostname is None or not len(hostname):
        hostname = get_remote_relation_attr(remote_unit, "private-address", relation_id)

    if hostname is None or not len(hostname):
        log("relation-get failed")

        return 2

    if check_ip(hostname):
        # Some providers don't provide hostnames, so use the remote unit name.
        ip_address = hostname
    else:
        ip_address = socket.getaddrinfo(hostname, None)[0][4][0]

    return (ip_address, remote_unit.replace("/", "-"))


def refresh_hostgroups():  # noqa:C901
    """Parse the on-disk hostgroups and regenerate, removing any old hosts."""
    hosts = [x["host_name"] for x in Model.Host.objects.all if x["host_name"]]

    hgroups = {}

    for host in hosts:
        hgroup_name = get_hostgroup_name(host)
        if hgroup_name is None:
            continue
        hgroups.setdefault(hgroup_name, []).append(host)

    # Find existing autogenerated
    auto_hgroups = Model.Hostgroup.objects.filter(notes__contains="#autogenerated#")
    auto_hgroups = [x.get_attribute("hostgroup_name") for x in auto_hgroups]

    # Delete the ones not in hgroups
    to_delete = set(auto_hgroups).difference(set(hgroups.keys()))

    for hgroup_name in to_delete:
        try:
            hgroup = Model.Hostgroup.objects.get_by_shortname(hgroup_name)
            hgroup.delete()
        except (ValueError, KeyError):
            pass

    for hgroup_name, members in hgroups.iteritems():
        if os.path.exists(get_nagios_hostgroup_config_path(hgroup_name)):
            # Skip updating files unrelated to the hook at hand unless they were
            # deliberately removed with the intent of them being rewritten.
            continue
        try:
            hgroup = Model.Hostgroup.objects.get_by_shortname(hgroup_name)
        except (ValueError, KeyError):
            hgroup = Model.Hostgroup()
            hgroup.set_filename(get_nagios_hostgroup_config_path(hgroup_name))
            hgroup.set_attribute("hostgroup_name", hgroup_name)
            hgroup.set_attribute("notes", "#autogenerated#")

        hgroup.set_attribute("members", ",".join(members))
        hgroup.save()


def _make_check_command(args):
    args = [str(arg) for arg in args]
    # There is some worry of collision, but the uniqueness of the initial
    # command should be enough.
    signature = REDUCE_RE.sub("_", "".join([os.path.basename(arg) for arg in args]))
    Model.Command.objects.reload_cache()
    try:
        cmd = Model.Command.objects.get_by_shortname(signature)
    except (ValueError, KeyError):
        cmd = Model.Command()
        cmd.set_attribute("command_name", signature)
        cmd.set_attribute("command_line", " ".join(args))
        cmd.save()

    return signature


def _extend_args(args, cmd_args, switch, value):
    args.append(value)
    cmd_args.extend((switch, '"$ARG%d$"' % len(args)))


def customize_http(service, name, extra):
    args = []
    cmd_args = []
    plugin = os.path.join(PLUGIN_PATH, "check_http")
    port = extra.get("port", 80)
    path = extra.get("path", "/")
    args = [port, path]
    cmd_args = [plugin, "-p", '"$ARG1$"', "-u", '"$ARG2$"']

    if "status" in extra:
        _extend_args(args, cmd_args, "-e", extra["status"])

    if "host" in extra:
        _extend_args(args, cmd_args, "-H", extra["host"])
        cmd_args.extend(("-I", "$HOSTADDRESS$"))
    else:
        cmd_args.extend(("-H", "$HOSTADDRESS$"))
    check_timeout = config("check_timeout")

    if check_timeout is not None:
        cmd_args.extend(("-t", check_timeout))
    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_mysql(service, name, extra):
    plugin = os.path.join(PLUGIN_PATH, "check_mysql")
    args = []
    cmd_args = [plugin, "-H", "$HOSTADDRESS$"]

    if "user" in extra:
        _extend_args(args, cmd_args, "-u", extra["user"])

    if "password" in extra:
        _extend_args(args, cmd_args, "-p", extra["password"])
    check_timeout = config("check_timeout")

    if check_timeout is not None:
        cmd_args.extend(("-t", check_timeout))
    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_pgsql(service, name, extra):
    plugin = os.path.join(PLUGIN_PATH, "check_pgsql")
    args = []
    cmd_args = [plugin, "-H", "$HOSTADDRESS$"]
    check_timeout = config("check_timeout")

    if check_timeout is not None:
        cmd_args.extend(("-t", check_timeout))
    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_nrpe(service, name, extra):
    plugin = os.path.join(PLUGIN_PATH, "check_nrpe")
    args = []
    cmd_args = [plugin, "-H", "$HOSTADDRESS$"]

    if name in ("mem", "swap"):
        cmd_args.extend(("-c", "check_%s" % name))
    elif "command" in extra:
        cmd_args.extend(("-c", extra["command"]))
    else:
        cmd_args.extend(("-c", extra))
    check_timeout = config("check_timeout")

    if check_timeout is not None:
        cmd_args.extend(("-t", check_timeout))
    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_rpc(service, name, extra):
    """Customize the check_rpc plugin to check things like nfs."""
    plugin = os.path.join(PLUGIN_PATH, "check_rpc")
    args = []
    # /usr/lib/nagios/plugins/check_rpc -H <host> -C <rpc_command>
    cmd_args = [plugin, "-H", "$HOSTADDRESS$"]

    if "rpc_command" in extra:
        cmd_args.extend(("-C", extra["rpc_command"]))

    if "program_version" in extra:
        cmd_args.extend(("-c", extra["program_version"]))

    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_tcp(service, name, extra):
    """Customize tcp can be used to check things like memcached."""
    plugin = os.path.join(PLUGIN_PATH, "check_tcp")
    args = []
    # /usr/lib/nagios/plugins/check_tcp -H <host> -E
    cmd_args = [plugin, "-H", "$HOSTADDRESS$", "-E"]

    if "port" in extra:
        cmd_args.extend(("-p", extra["port"]))

    if "string" in extra:
        cmd_args.extend(("-s", "'{}'".format(extra["string"])))

    if "expect" in extra:
        cmd_args.extend(("-e", extra["expect"]))

    if "warning" in extra:
        cmd_args.extend(("-w", extra["warning"]))

    if "critical" in extra:
        cmd_args.extend(("-c", extra["critical"]))

    if "timeout" in extra:
        cmd_args.extend(("-t", extra["timeout"]))
    check_timeout = config("check_timeout")

    if check_timeout is not None:
        cmd_args.extend(("-t", check_timeout))

    check_command = _make_check_command(cmd_args)
    cmd = "%s!%s" % (check_command, "!".join([str(x) for x in args]))
    service.set_attribute("check_command", cmd)

    return True


def customize_service(service, family, name, extra):
    """Map names to service methods.

    The monitors.yaml names are mapped to methods that customize services.
    """
    customs = {
        "http": customize_http,
        "mysql": customize_mysql,
        "nrpe": customize_nrpe,
        "tcp": customize_tcp,
        "rpc": customize_rpc,
        "pgsql": customize_pgsql,
    }

    if family in customs:
        return customs[family](service, name, extra)

    return False


def update_localhost():
    """Update the localhost definition to use the ubuntu icons."""
    Model.cfg_file = MAIN_NAGIOS_CFG
    Model.pynag_directory = os.path.join(MAIN_NAGIOS_DIR, "conf.d")
    hosts = Model.Host.objects.filter(host_name="localhost", object_type="host")

    for host in hosts:
        host.icon_image = "base/ubuntu.png"
        host.icon_image_alt = "Ubuntu Linux"
        host.vrml_image = "ubuntu.png"
        host.statusmap_image = "base/ubuntu.gd2"
        host.save()


def get_pynag_host(target_id, owner_unit=None, owner_relation=None):
    try:
        host = Model.Host.objects.get_by_shortname(target_id)
    except (ValueError, KeyError):
        host = Model.Host()
        host.set_filename(get_nagios_host_config_path(target_id))
        host.set_attribute("host_name", target_id)
        host.set_attribute("use", "generic-host")
        # Adding the ubuntu icon image definitions to the host.
        host.set_attribute("icon_image", "base/ubuntu.png")
        host.set_attribute("icon_image_alt", "Ubuntu Linux")
        host.set_attribute("vrml_image", "ubuntu.png")
        host.set_attribute("statusmap_image", "base/ubuntu.gd2")
        host.save()
        host = Model.Host.objects.get_by_shortname(target_id)
    apply_host_policy(target_id, owner_unit, owner_relation)

    return host


def get_pynag_service(target_id, service_name):
    services = Model.Service.objects.filter(
        host_name=target_id, service_description=service_name
    )

    if len(services) == 0:
        service = Model.Service()
        service.set_filename(get_nagios_host_config_path(target_id))
        service.set_attribute("service_description", service_name)
        service.set_attribute("host_name", target_id)
        service.set_attribute("use", "generic-service")
    else:
        service = services[0]

    return service


def get_nagios_host_config_path(target_id):
    return HOST_TEMPLATE.format(sanitize_nagios_name(target_id))


def get_nagios_hostgroup_config_path(hostgroup_name):
    return HOSTGROUP_TEMPLATE.format(sanitize_nagios_name(hostgroup_name))


def sanitize_nagios_name(name):
    """Sanitize host[group] name for use in a filename.

    Methodology: preserve the original information in the name, but escape problematic
    characters.  The original name should be retrievable, in case there's a need to
    reverse this in the future.

    Assumption: escaped characters are within codepoints 0x00 to 0x7F (i.e. ASCII), and
    can be represented as a 3 character sequence, including the escape character.

    """
    for char in SANITIZE_CHARS:
        replacement = SANITIZE_ESCAPE_CHAR + "{:02x}".format(ord(char))
        name = name.replace(char, replacement)
    return name


def apply_host_policy(target_id, owner_unit, owner_relation):
    ssh_service = get_pynag_service(target_id, "SSH")
    ssh_service.set_attribute("check_command", "check_ssh")
    ssh_service.save()


def _replace_in_config(find_me, replacement):
    with open(INPROGRESS_CFG) as cf:
        with tempfile.NamedTemporaryFile(dir=INPROGRESS_DIR, delete=False) as new_cf:
            for line in cf:
                new_cf.write(line.replace(find_me, replacement))
            new_cf.flush()
            os.chmod(new_cf.name, 0o644)
            os.unlink(INPROGRESS_CFG)
            os.rename(new_cf.name, INPROGRESS_CFG)


def _commit_in_config(find_me, replacement):
    with open(MAIN_NAGIOS_CFG) as cf:
        with tempfile.NamedTemporaryFile(dir=MAIN_NAGIOS_DIR, delete=False) as new_cf:
            for line in cf:
                new_cf.write(line.replace(find_me, replacement))
            new_cf.flush()
            os.chmod(new_cf.name, 0o644)
            os.unlink(MAIN_NAGIOS_CFG)
            os.rename(new_cf.name, MAIN_NAGIOS_CFG)


def initialize_inprogress_config(full_rewrite=False):
    if os.path.exists(INPROGRESS_DIR):
        shutil.rmtree(INPROGRESS_DIR)
    shutil.copytree(MAIN_NAGIOS_DIR, INPROGRESS_DIR)
    _replace_in_config(MAIN_NAGIOS_DIR, INPROGRESS_DIR)

    # Build list of files to replace/remove.
    paths_to_remove = [OLD_CHARM_CFG]
    if full_rewrite:
        paths_to_remove.extend(_get_all_related_config_paths())
    else:
        paths_to_remove.extend(_get_minimal_related_config_paths())
    for path in paths_to_remove:
        if os.path.exists(path):
            os.unlink(path)


def _get_all_related_config_paths():
    paths_to_remove = []
    for template in HOST_TEMPLATE, HOSTGROUP_TEMPLATE:
        paths_to_remove.extend(glob.glob(template.format("*")))
    return paths_to_remove


def _get_minimal_related_config_paths():
    # Note: This also gets called via the config-changed hook, so there may not be any
    # implicit relation data; skip in this case.
    target_id = None
    relation_data = relation_get()
    if relation_data:
        target_id = relation_data.get(TARGET_ID_KEY)
    if target_id is None:
        return []

    hgroup_name = get_hostgroup_name(target_id)
    checks = [(target_id, HOST_TEMPLATE)]
    if hgroup_name is not None:
        checks.append((hgroup_name, HOSTGROUP_TEMPLATE))

    paths_to_remove = []
    for target_name, template in checks:
        # Per-host and per-hostgroup config files related to the current hook's
        # remote unit.  This is complicated due to automatic de-duplication of
        # duplicate host names from different models.
        # Case 1: Direct match
        paths_to_remove.append(template.format(target_name))
        # Case 2: Deduped match, main algorithm, glob on first 7 chars of sha256sum
        if MODEL_ID_KEY in relation_data:
            model_id = relation_data[MODEL_ID_KEY]
            sha_prefix = get_model_id_sha(model_id)[:HOST_PREFIX_MIN_LENGTH]
            glob_pattern = template.format("{}*_{}".format(sha_prefix, target_name))
            paths_to_remove.extend(glob.glob(glob_pattern))
        # Case 3: Deduped match, fallback algorithm
        relid_unit_prefix = get_relid_unit_prefix(relation_id(), remote_unit())
        paths_to_remove.append(
            template.format("{}_{}".format(relid_unit_prefix, target_name))
        )
    return paths_to_remove


def get_hostgroup_name(hostname):
    """Given a hostname, return the associated hostgroup's name.

    This function may return None if there is no clear hostgroup name to extract.

    """
    try:
        hostgroup_name, _ = hostname.rsplit("-", 1)
    except ValueError:
        hostgroup_name = None
    return hostgroup_name


def get_model_id_sha(model_id):
    return hashlib.sha256(model_id.encode()).hexdigest()


def get_relid_unit_prefix(relation_id, unit):
    return "{}_{}".format(relation_id, unit.split("/")[-1])


def flush_inprogress_config():
    if not os.path.exists(INPROGRESS_DIR):
        return

    if os.path.exists(MAIN_NAGIOS_BAK):
        shutil.rmtree(MAIN_NAGIOS_BAK)

    if os.path.exists(MAIN_NAGIOS_DIR):
        shutil.move(MAIN_NAGIOS_DIR, MAIN_NAGIOS_BAK)
    shutil.move(INPROGRESS_DIR, MAIN_NAGIOS_DIR)
    # now that directory has been changed need to update the config file to
    # reflect the real stuff..
    _commit_in_config(INPROGRESS_DIR, MAIN_NAGIOS_DIR)
