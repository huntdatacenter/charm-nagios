#!/usr/bin/env python3

import subprocess
from charmhelpers.core import hookenv

NAGIOS_SERVICE = "nagios4"


is_active = subprocess.run(
    ["systemctl", "is-active", NAGIOS_SERVICE],
    capture_output=True
).stdout.decode().strip() == "active"

is_failed = subprocess.run(
    ["systemctl", "is-failed", NAGIOS_SERVICE],
    capture_output=True
).stdout.decode().strip() != "active"

if is_active:
    hookenv.status_set('active', 'ready')
elif is_failed:
    hookenv.status_set('active', 'error')
