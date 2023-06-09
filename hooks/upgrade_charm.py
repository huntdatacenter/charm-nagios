#!/usr/bin/env python3

# Rewritten from bash to python 3/2/2014 for charm helper inclusion
# of SSL-Everywhere!
import base64
import errno
import glob
import grp
import os
import pwd
import shutil
import stat
import string
import subprocess
from hashlib import md5

try:
    from enum import Enum  # noqa: F401
except ImportError:
    subprocess.check_call(
        "DEBIAN_FRONTEND=noninteractive apt-get -qy install python-enum34", shell=True
    )

from application_dashboard_relation import application_dashboard_relation_changed

from charmhelpers import fetch
from charmhelpers.contrib import ssl
from charmhelpers.core import hookenv, host

from jinja2 import Template

import yaml

from common import (
    reload_nagios,
    update_localhost,
    update_notification_interval,
    update_notification_options,
)

# Gather facts
legacy_relations = hookenv.config("legacy")
extra_config = hookenv.config("extraconfig")
enable_livestatus = hookenv.config("enable_livestatus")
livestatus_path = hookenv.config("livestatus_path")
enable_pagerduty = hookenv.config("enable_pagerduty")
pagerduty_key = hookenv.config("pagerduty_key")
pagerduty_path = hookenv.config("pagerduty_path")
notification_levels = hookenv.config("pagerduty_notification_levels")
nagios_user = hookenv.config("nagios_user")
nagios_group = hookenv.config("nagios_group")
ssl_config = str(hookenv.config("ssl")).lower()
charm_dir = os.environ["CHARM_DIR"]
cert_domain = hookenv.unit_get("public-address")
nagios_cfg = "/etc/nagios4/nagios.cfg"
nagios_cgi_cfg = "/etc/nagios4/cgi.cfg"
pagerduty_cfg = "/etc/nagios4/conf.d/pagerduty_nagios.cfg"
traps_cfg = "/etc/nagios4/conf.d/traps.cfg"
pagerduty_cron = "/etc/cron.d/nagios-pagerduty-flush"
password = hookenv.config("password")
ro_password = hookenv.config("ro-password")
nagiosadmin = hookenv.config("nagiosadmin") or "nagiosadmin"
contactgroup_members = hookenv.config("contactgroup-members")

livestatus_xinetd_path = "/etc/xinetd.d/livestatus"
livestatus_enable_xinetd = hookenv.config("livestatus_enable_xinetd")
livestatus_xinetd_port = hookenv.config("livestatus_xinetd_port")
livestatus_xinetd_only_from = hookenv.config("livestatus_xinetd_only_from")


# this global var will collect contactgroup members that must be forced
# it will be changed by functions
forced_contactgroup_members = []

HTTP_ENABLED = ssl_config not in ["only"]
SSL_CONFIGURED = ssl_config in ["on", "only", "true"]


def warn_legacy_relations():
    """Check the charm relations for legacy relations.

    Inserts warnings into the log about legacy relations, as they will be removed
    in the future
    """
    if legacy_relations is not None:
        hookenv.log(
            "Relations have been radically changed."
            " The monitoring interface is not supported anymore.",
            "WARNING",
        )
    hookenv.log("Please use the generic juju-info or the monitors interface", "WARNING")


def parse_extra_contacts(yaml_string):
    """Parse a list of extra Nagios contacts from a YAML string.

    Does basic sanitization only
    """
    # Final result
    extra_contacts = []

    # Valid characters for contact names
    valid_name_chars = string.ascii_letters + string.digits + "_-"

    try:
        extra_contacts_raw = yaml.load(yaml_string, Loader=yaml.SafeLoader) or []

        if not isinstance(extra_contacts_raw, list):
            raise ValueError("not a list")

        for contact in extra_contacts_raw:
            if {"name", "host", "service"} > set(contact.keys()):
                hookenv.log(
                    "Contact {} is missing fields.".format(contact), hookenv.WARNING
                )

                continue

            if set(contact["name"]) > set(valid_name_chars):
                hookenv.log(
                    "Contact name {} is illegal".format(contact["name"]),
                    hookenv.WARNING,
                )

                continue

            if "\n" in (contact["host"] + contact["service"]):
                hookenv.log("Line breaks not allowed in commands", hookenv.WARNING)

                continue

            contact["name"] = contact["name"].lower()
            contact["alias"] = contact["name"].capitalize()
            extra_contacts.append(contact)

    except (ValueError, yaml.error.YAMLError) as e:
        hookenv.log(
            'Invalid "extra_contacts" configuration: {}'.format(e), hookenv.WARNING
        )

    if len(extra_contacts_raw) != len(extra_contacts):
        hookenv.log(
            "Invalid extra_contacts config, found {} contacts defined, "
            "only {} were valid, check unit logs for "
            "detailed errors".format(len(extra_contacts_raw), len(extra_contacts))
        )

    return extra_contacts


# If the charm has extra configuration provided, write that to the
# proper nagios4 configuration file, otherwise remove the config
def write_extra_config():
    # Be predjudice about this - remove the file always.

    if host.file_hash("/etc/nagios4/conf.d/extra.cfg") is not None:
        os.remove("/etc/nagios4/conf.d/extra.cfg")
    # If we have a config, then write it. the hook reconfiguration will
    # handle the details

    if extra_config is not None:
        host.write_file("/etc/nagios4/conf.d/extra.cfg", extra_config)


# Equivalent of mkdir -p, since we can't rely on
# python 3.2 os.makedirs exist_ok argument
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


# Fix the path to be world executable
def fixpath(path):
    if os.path.isdir(path):
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXOTH)

    if path != "/":
        fixpath(os.path.split(path)[0])


def enable_livestatus_config():
    livestatus_dir = os.path.dirname(livestatus_path)
    hookenv.log("Livestatus is enabled")
    if not os.path.isdir(livestatus_dir):
        hookenv.log("Making path for livestatus_dir")
        mkdir_p(livestatus_dir)
    # fix perms on livestatus dir
    hookenv.log("Fixing perms on livestatus_path")
    fixpath(livestatus_dir)
    fetch.apt_update()
    missing_packages = fetch.filter_installed_packages(["check-mk-livestatus"])
    if missing_packages:
        fetch.apt_install(missing_packages)

    if not os.path.exists(livestatus_path):
        # This needs a nagios restart to actually make the socket
        reload_nagios()
    # Fix the perms on the socket
    hookenv.log("Fixing perms on the socket")
    uid = pwd.getpwnam(nagios_user).pw_uid
    gid = grp.getgrnam("www-data").gr_gid
    os.chown(livestatus_path, uid, gid)
    os.chown(livestatus_dir, uid, gid)
    st = os.stat(livestatus_path)
    os.chmod(livestatus_path, st.st_mode | stat.S_IRGRP)
    os.chmod(
        livestatus_dir,
        st.st_mode | stat.S_IRGRP | stat.S_ISGID | stat.S_IXUSR | stat.S_IXGRP,
    )


def enable_pagerduty_config():
    global forced_contactgroup_members

    if enable_pagerduty:
        hookenv.log("Pagerduty is enabled")
        fetch.apt_update()
        fetch.apt_install("libhttp-parser-perl")
        env = os.environ
        proxy = env.get("JUJU_CHARM_HTTPS_PROXY") or env.get("https_proxy")
        proxy_switch = "--proxy {}".format(proxy) if proxy else ""

        # Ship the pagerduty_nagios.cfg file
        template_values = {
            "pagerduty_key": pagerduty_key,
            "pagerduty_path": pagerduty_path,
            "proxy_switch": proxy_switch,
            "notification_levels": notification_levels,
        }

        with open("hooks/templates/pagerduty_nagios_cfg.tmpl", "r") as f:
            template_def = f.read()

        t = Template(template_def)
        with open(pagerduty_cfg, "w") as f:
            f.write(t.render(template_values))

        with open("hooks/templates/nagios-pagerduty-flush-cron.tmpl", "r") as f2:
            template_def = f2.read()

        t2 = Template(template_def)
        with open(pagerduty_cron, "w") as f2:
            f2.write(t2.render(template_values))

        # Ship the pagerduty_nagios.py script
        shutil.copy("files/pagerduty_nagios.py", "/usr/local/bin/pagerduty_nagios.py")

        # Create the pagerduty queue dir

        if not os.path.isdir(pagerduty_path):
            hookenv.log("Making path for pagerduty_path")
            mkdir_p(pagerduty_path)
        # Fix the perms on it
        uid = pwd.getpwnam(nagios_user).pw_uid
        gid = grp.getgrnam(nagios_group).gr_gid
        os.chown(pagerduty_path, uid, gid)
    else:
        # Clean up the files if we don't want pagerduty

        if os.path.isfile(pagerduty_cfg):
            os.remove(pagerduty_cfg)

        if os.path.isfile(pagerduty_cron):
            os.remove(pagerduty_cron)

    # Update contacts for admin

    if enable_pagerduty:
        # avoid duplicates

        if "pagerduty" not in contactgroup_members:
            forced_contactgroup_members.append("pagerduty")


def enable_traps_config():
    global forced_contactgroup_members

    send_traps_to = hookenv.config("send_traps_to")

    if not send_traps_to:
        if os.path.isfile(traps_cfg):
            os.remove(traps_cfg)
        hookenv.log("Send traps feature is disabled")

        return

    hookenv.log("Send traps feature is enabled, target address is %s" % send_traps_to)

    if "managementstation" not in contactgroup_members:
        forced_contactgroup_members.append("managementstation")

    template_values = {
        "send_traps_to": send_traps_to,
        "traps_service_notification_options": hookenv.config(
            "traps_service_notification_options"
        ),
        "traps_host_notification_options": hookenv.config(
            "traps_host_notification_options"
        ),
    }

    with open("hooks/templates/traps.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open(traps_cfg, "w") as f:
        f.write(t.render(template_values))


def update_commands():
    max_notifications = hookenv.config("email_max_notifications")
    if not isinstance(max_notifications, int) or max_notifications < 0:
        # Invalid value; coerce to default of 0 (max notifications disabled)
        max_notifications = 0

    host_mail_limiter = ""
    service_mail_limiter = ""
    if max_notifications:
        host_mail_limiter = "test $HOSTNOTIFICATIONNUMBER$ -le {} && ".format(
            max_notifications
        )
        service_mail_limiter = "test $SERVICENOTIFICATIONNUMBER$ -le {} && ".format(
            max_notifications
        )

    template_values = {
        "host_mail_limiter": host_mail_limiter,
        "service_mail_limiter": service_mail_limiter,
        "sender_email": hookenv.config("sender_email").strip(),
    }

    with open("hooks/templates/commands-cfg.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open("/etc/nagios4/commands.cfg", "w") as f:
        f.write(t.render(template_values))

    reload_nagios()


def update_contacts():
    # Multiple Email Contacts
    admin_members = ""
    contacts = []
    admin_email = list(filter(None, set(hookenv.config("admin_email").split(","))))

    if len(admin_email) == 0:
        hookenv.log("admin_email is unset, this isn't valid config")
        hookenv.status_set("blocked", "admin_email is not configured")
        exit(1)
    hookenv.status_set("active", "ready")

    if len(admin_email) == 1:
        hookenv.log("Setting one admin email address '%s'" % admin_email[0])
        contacts = [{"contact_name": "root", "alias": "Root", "email": admin_email[0]}]
    elif len(admin_email) > 1:
        hookenv.log("Setting %d admin email addresses" % len(admin_email))
        contacts = []

        for email in admin_email:
            contact_name = email.replace("@", "").replace(".", "").lower()
            contact_alias = contact_name.capitalize()
            contacts.append(
                {"contact_name": contact_name, "alias": contact_alias, "email": email}
            )

        admin_members = ", ".join([c["contact_name"] for c in contacts])

    resulting_members = contactgroup_members

    if admin_members:
        # if multiple admin emails are passed ignore contactgroup_members
        resulting_members = admin_members

    if forced_contactgroup_members:
        resulting_members = ",".join([resulting_members] + forced_contactgroup_members)

    # Parse extra_contacts
    extra_contacts = parse_extra_contacts(hookenv.config("extra_contacts"))

    template_values = {
        "admin_service_notification_period": hookenv.config(
            "admin_service_notification_period"
        ),
        "admin_host_notification_period": hookenv.config(
            "admin_host_notification_period"
        ),
        "admin_service_notification_options": hookenv.config(
            "admin_service_notification_options"
        ),
        "admin_host_notification_options": hookenv.config(
            "admin_host_notification_options"
        ),
        "admin_service_notification_commands": hookenv.config(
            "admin_service_notification_commands"
        ),
        "admin_host_notification_commands": hookenv.config(
            "admin_host_notification_commands"
        ),
        "contacts": contacts,
        "contactgroup_members": resulting_members,
        "extra_contacts": extra_contacts,
    }

    with open("hooks/templates/contacts-cfg.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open("/etc/nagios4/conf.d/contacts_nagios2.cfg", "w") as f:
        f.write(t.render(template_values))

    reload_nagios()


def ssl_configured():
    allowed_options = ["on", "only"]

    if str(ssl_config).lower() in allowed_options:
        return True

    return False


# Gather local facts for SSL deployment
deploy_key_path = os.path.join(charm_dir, "data", "%s.key" % (cert_domain))
deploy_cert_path = os.path.join(charm_dir, "data", "%s.crt" % (cert_domain))
deploy_csr_path = os.path.join(charm_dir, "data", "%s.csr" % (cert_domain))
# set basename for SSL key locations
cert_file = "/etc/ssl/certs/%s.pem" % (cert_domain)
key_file = "/etc/ssl/private/%s.key" % (cert_domain)
chain_file = "/etc/ssl/certs/%s.csr" % (cert_domain)


# Check for key and certificate, since the CSR is optional
# leave it out of the dir file check and let the config manager
# worry about it
def check_ssl_files():
    key = os.path.exists(deploy_key_path)
    cert = os.path.exists(deploy_cert_path)

    if key is False or cert is False:
        return False

    return True


# Decode the SSL keys from their base64 encoded values in the configuration
def decode_ssl_keys():
    if hookenv.config("ssl_key"):
        hookenv.log("Writing key from config ssl_key: %s" % key_file)
        with open(key_file, "w") as f:
            f.write(str(base64.b64decode(hookenv.config("ssl_key"))))

    if hookenv.config("ssl_cert"):
        with open(cert_file, "w") as f:
            f.write(str(base64.b64decode(hookenv.config("ssl_cert"))))

    if hookenv.config("ssl_chain"):
        with open(chain_file, "w") as f:
            f.write(str(base64.b64decode(hookenv.config("ssl_cert"))))


def enable_ssl():
    # Set the basename of all ssl files

    # Validate that we have configs, and generate a self signed certificate.

    if not hookenv.config("ssl_cert"):
        # bail if keys already exist

        if os.path.exists(cert_file):
            hookenv.log("Keys exist, not creating keys!", "WARNING")

            return
        # Generate a self signed key using CharmHelpers
        hookenv.log("Generating Self Signed Certificate", "INFO")
        ssl.generate_selfsigned(key_file, cert_file, cn=cert_domain)
    else:
        decode_ssl_keys()
        hookenv.log("Decoded SSL files", "INFO")


def nagios_bool(value):
    """Convert a Python boolean into Nagios 0/1 integer representation."""
    return int(value)


def update_config():
    host_context = hookenv.config("nagios_host_context")
    local_host_name = "nagios"
    principal_unitname = hookenv.principal_unit()
    # Fallback to using "primary" if it exists.

    if principal_unitname:
        local_host_name = principal_unitname
    else:
        local_host_name = hookenv.local_unit().replace("/", "-")
    template_values = {
        "nagios_user": nagios_user,
        "nagios_group": nagios_group,
        "enable_livestatus": enable_livestatus,
        "livestatus_path": livestatus_path,
        "livestatus_args": hookenv.config("livestatus_args"),
        "check_external_commands": hookenv.config("check_external_commands"),
        "command_file": hookenv.config("command_file"),
        "debug_file": hookenv.config("debug_file"),
        "debug_verbosity": hookenv.config("debug_verbosity"),
        "debug_level": hookenv.config("debug_level"),
        "daemon_dumps_core": hookenv.config("daemon_dumps_core"),
        "flap_detection": nagios_bool(hookenv.config("flap_detection")),
        "admin_email": hookenv.config("admin_email"),
        "admin_pager": hookenv.config("admin_pager"),
        "log_rotation_method": hookenv.config("log_rotation_method"),
        "log_archive_path": hookenv.config("log_archive_path"),
        "use_syslog": hookenv.config("use_syslog"),
        "monitor_self": hookenv.config("monitor_self"),
        "nagios_hostname": "{}-{}".format(host_context, local_host_name),
        "load_monitor": hookenv.config("load_monitor"),
        "is_container": host.is_container(),
        "service_check_timeout": hookenv.config("service_check_timeout"),
        "service_check_timeout_state": hookenv.config("service_check_timeout_state"),
    }

    with open("hooks/templates/nagios-cfg.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open(nagios_cfg, "w") as f:
        f.write(t.render(template_values))

    with open("hooks/templates/localhost_nagios2.cfg.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open("/etc/nagios4/conf.d/localhost_nagios2.cfg", "w") as f:
        f.write(t.render(template_values))

    reload_nagios()


def update_cgi_config():
    template_values = {"nagiosadmin": nagiosadmin, "ro_password": ro_password}
    with open("hooks/templates/nagios-cgi.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open(nagios_cgi_cfg, "w") as f:
        f.write(t.render(template_values))

    reload_nagios()
    host.service_reload("apache2")


# Nagios3 is deployed as a global apache application from the archive.
# We'll get a little funky and add the SSL keys to the default-ssl config
# which sets our keys, including the self-signed ones, as the host keyfiles.
# note: i tried to use cheetah, and it barfed, several times. It can go play
# in a fire. I'm jusing jinja2.
def update_apache():
    """Add SSL keys to default-ssl config.

    Nagios3 is deployed as a global apache application from the archive.
    We'll get a little funky and add the SSL keys to the default-ssl config
    which sets our keys, including the self-signed ones, as the host keyfiles.
    """
    # Start by Setting the ports.conf

    with open("hooks/templates/ports-cfg.jinja2", "r") as f:
        template_def = f.read()
    t = Template(template_def)
    ports_conf = "/etc/apache2/ports.conf"

    with open(ports_conf, "w") as f:
        f.write(t.render({"enable_http": HTTP_ENABLED}))

    # Next setup the default-ssl.conf

    if os.path.exists(chain_file) and os.path.getsize(chain_file) > 0:
        ssl_chain = chain_file
    else:
        ssl_chain = None
    template_values = {
        "ssl_key": key_file,
        "ssl_cert": cert_file,
        "ssl_chain": ssl_chain,
    }
    with open("hooks/templates/default-ssl.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    ssl_conf = "/etc/apache2/sites-available/default-ssl.conf"
    with open(ssl_conf, "w") as f:
        f.write(t.render(template_values))

    # Create directory for extra *.include files installed by subordinates
    try:
        os.makedirs("/etc/apache2/vhost.d/")
    except OSError:
        pass

    # Configure the behavior of http sites
    sites = glob.glob("/etc/apache2/sites-available/*.conf")
    non_ssl = set(sites) - {ssl_conf}

    for each in non_ssl:
        site = os.path.basename(each).rsplit(".", 1)[0]
        Apache2Site(site).action(enabled=HTTP_ENABLED)

    # Configure the behavior of https site
    Apache2Site("default-ssl").action(enabled=SSL_CONFIGURED)

    # Finally, restart apache2
    host.service_reload("apache2")


class Apache2Site:
    def __init__(self, site):
        self.site = site
        self.is_ssl = "ssl" in site.lower()
        self.port = 443 if self.is_ssl else 80

    def action(self, enabled):
        return self._enable() if enabled else self._disable()

    def _call(self, args):
        try:
            subprocess.check_output(args, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            hookenv.log(
                "Apache2Site: `{}`, returned {}, stdout:\n{}".format(
                    e.cmd, e.returncode, e.output
                ),
                "ERROR",
            )

    def _enable(self):
        hookenv.log("Apache2Site: Enabling %s..." % self.site, "INFO")
        self._call(["a2ensite", self.site])

        if self.port == 443:
            self._call(["a2enmod", "ssl"])
        hookenv.open_port(self.port)

    def _disable(self):
        hookenv.log("Apache2Site: Disabling %s..." % self.site, "INFO")
        self._call(["a2dissite", self.site])
        hookenv.close_port(self.port)


def ht5(x):
    return md5(':'.join(x).encode()).hexdigest()


def generate_htdigest(account, password, realm):
    htpwd = ':'.join([account, realm, ht5([account, realm, password])])
    return htpwd


def update_password(account, password, realm="Restricted Nagios Access Zone"):
    """Update the charm and Apache's record of the password for the supplied account."""
    account_file = "".join(["/var/lib/juju/nagios.", account, ".passwd"])

    if password:
        with open(account_file, "w") as f:
            f.write(password)
            os.fchmod(f.fileno(), 0o0400)
        with open("/etc/nagios4/htdigest.users", "a+") as f:
            f.write(generate_htdigest(account, password, realm))
            f.write("\n")
        # subprocess.call([
        #     "htpasswd", "-b", "/etc/nagios4/htpasswd.users",
        #     account,
        #     password
        # ])
    else:
        """password was empty, it has been removed. We should delete the account"""
        os.path.isfile(account_file) and os.remove(account_file)
        # subprocess.call(["htpasswd", "-D", "/etc/nagios4/htpasswd.users", account])
        subprocess.call([
            "sed", "-i", "/^{account}:/d".format(account=account),
            "/etc/nagios4/htdigest.users"])


def configure_livestatus_xinetd():
    if not enable_livestatus or not livestatus_enable_xinetd:
        hookenv.log("Livestatus xinetd not enabled, skipping...", "INFO")
        return

    hookenv.log("Configuring livestatus xinetd...", "INFO")

    fetch.apt_update()
    fetch.apt_install("xinetd")

    template_values = {
        "livestatus_path": livestatus_path,
        "xinetd_port": livestatus_xinetd_port,
        "xinetd_only_from": livestatus_xinetd_only_from,
    }

    with open("hooks/templates/livestatus.tmpl", "r") as f:
        template_def = f.read()

    t = Template(template_def)
    with open(livestatus_xinetd_path, "w") as f:
        f.write(t.render(template_values))

    host.service_reload("xinetd")
    hookenv.log("Livestatus xinetd configured.", "INFO")


warn_legacy_relations()
write_extra_config()
# enable_traps_config and enable_pagerduty_config modify forced_contactgroup_members
# they need to run before update_contacts that will consume that global var.
enable_traps_config()

if ssl_configured():
    enable_ssl()
enable_pagerduty_config()
update_commands()
update_contacts()
update_config()
update_apache()
update_localhost()
update_notification_interval()
update_notification_options()
update_cgi_config()
update_contacts()
update_password("nagiosro", ro_password)
configure_livestatus_xinetd()
application_dashboard_relation_changed()

if password:
    update_password(nagiosadmin, password)

if nagiosadmin != "nagiosadmin":
    update_password("nagiosadmin", False)
if enable_livestatus:
    enable_livestatus_config()

subprocess.call(["scripts/postfix_loopback_only.sh"])
subprocess.call(["hooks/mymonitors-relation-joined"])
subprocess.call(["hooks/monitors-relation-changed"])
