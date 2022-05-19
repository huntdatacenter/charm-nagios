#!/usr/bin/env python3

"""pagerduty_nagios.py - a Python 3 port of the pagerduty_nagios.pl script

PagerDuty's repo for the perl version:
https://github.com/PagerDuty/pagerduty-nagios-pl

Reasons for doing this:

* Easier for us to change due to langauge familiarity.

* Ability to make alerts more meaningful to us by only including the Nagios
  values we care about.

Help strings are mostly just copied from the Perl version of the script.

"""

# Original license of the Perl version of the script:
# ===================================================
#
# Copyright (c) 2011, PagerDuty, Inc. <info@pagerduty.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of PagerDuty Inc nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL PAGERDUTY INC BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


import argparse
import fcntl
import logging
import logging.handlers
import os
import re
import socket
import sys
import time
import traceback
from urllib.error import HTTPError
from urllib.request import urlopen, Request
from urllib.parse import urlencode

TIMEOUT_S = 10


def main():
    args = parse_args()
    handle_proxy(args)
    configure_logging(args.verbose)
    try:
        os.makedirs(args.queue_dir, exist_ok=True)
        if args.command == "enqueue":
            enqueue_event(args)
            lock_and_flush_queue(args)
        elif args.command == "flush":
            lock_and_flush_queue(args)
    except Exception:
        logging.error(traceback.format_exc())
        if not args.verbose:
            print("An error occurred; check syslog for details", file=sys.stderr)


def parse_args():
    ap = argparse.ArgumentParser(
        description="""\
This script passes events from Nagios to the PagerDuty alert system. It's
meant to be run as a Nagios notification plugin. For more details, please see
the PagerDuty Nagios integration docs at:
http://www.pagerduty.com/docs/nagios-integration.

When called in the "enqueue" mode, the script loads a Nagios notification out
of the environment and into the event queue.  It then tries to flush the
queue by sending any enqueued events to the PagerDuty server.  The script is
typically invoked in this mode from a Nagios notification handler.

When called in the "flush" mode, the script simply tries to send any enqueued
events to the PagerDuty server.  This mode is typically invoked by cron.  The
purpose of this mode is to retry any events that couldn't be sent to the
PagerDuty server for whatever reason when they were initially enqueued.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("command", choices=["enqueue", "flush"])
    ap.add_argument(
        "-a",
        "--api-base",
        default="https://events.pagerduty.com/nagios/2010-04-15",
        help="The base URL used to communicate with PagerDuty.  "
        "The default option here should be fine, but adjusting it "
        "may make sense if your firewall doesn't pass HTTPS "
        "traffic for some reason.  See the PagerDuty Nagios "
        "integration docs for details.",
    )
    ap.add_argument(
        "-q",
        "--queue-dir",
        default="/tmp/pagerduty_nagios",
        help="Path to the directory to use to store the event "
        "queue.  Default: %(default)s",
    )
    ap.add_argument(
        "-f",
        "--field",
        default=[],
        nargs="+",
        help="Add this key-value pair to the event being passed "
        "to PagerDuty.  The script automatically gathers Nagios "
        "macros out of the environment, so there's no need to "
        "specify these explicitly.  This option can be repeated "
        "as many times as necessary to pass multiple key-value "
        "pairs.  This option is only useful when an event is "
        "being enqueued.",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="store_true",
        help="Turn on extra debugging information.  Useful " "for debugging.",
    )
    ap.add_argument(
        "-A",
        "--all-variables",
        default=False,
        action="store_true",
        help="Include all NAGIOS_/ICINGA_ environment variables "
        "in raised events.  If not set, this script will "
        "only include a reduced set of these variables.",
    )
    ap.add_argument(
        "--ignore-extended-notifications",
        default=False,
        action="store_true",
        help="If set, this script will not attempt to coerce notification "
        'types starting with "FLAPPING" or "DOWNTIME".',
    )
    ap.add_argument(
        "-p",
        "--proxy",
        default="",
        help="Use a proxy for the connections like " '"--proxy http://127.0.0.1:8888/"',
    )
    return ap.parse_args()


def handle_proxy(args):
    if args.proxy:
        # Quick and dirty:
        # Rely on env vars for automatic urllib.request.ProxyHandler
        os.environ["http_proxy"] = args.proxy
        os.environ["https_proxy"] = args.proxy


def configure_logging(verbose):
    handlers = [
        logging.handlers.SysLogHandler(
            address="/dev/log",
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        ),
    ]
    if verbose:
        handlers.append(logging.StreamHandler(stream=sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(filename)s[%(levelname)s][%(process)s] %(message)s",
        handlers=handlers,
    )


REQUIRED_KEYS = [
    "CONTACTPAGER",  # The nagios key is provided here.
    "HOSTNAME",
    "NOTIFICATIONTYPE",  # Value is RECOVERY or PROBLEM.
    # PROBLEM creates alerts, RECOVERY clears them.
    "SERVICESTATE",
    "SERVICEDESC",
    # NOTE: pd_nagios_object is also required, but that's provided at the
    # command line via the -f/--field argument when invoked via Nagios.
]

RECOMMENDED_KEYS = [
    "SERVICEOUTPUT",  # The text of the alert.  Placed just above the arg
    # table in the Web UI.
    "HOSTADDRESS",  # IP address of the host
    "SHORTDATETIME",  # Timestamp of the event (doesn't specify time zone)
    "LONGDATETIME",  # Timestamp of the event (long version, specifies UTC)
]


def enqueue_event(args):
    event = {}
    for key in os.environ.keys():
        if key.startswith("NAGIOS_") or key.startswith("ICINGA_"):
            short_key = key.split("_", 1)[1]
            if (
                args.all_variables
                or short_key in REQUIRED_KEYS
                or short_key in RECOMMENDED_KEYS
            ):
                event[short_key] = os.environ[key]
    for field in args.field:
        field_key, field_value = field.split("=", 1)
        event[field_key] = field_value
    event["pd_version"] = "1.0"

    if not args.ignore_extended_notifications:
        # Coerce NOTIFICATIONTYPE as appropriate.
        #
        # DOWNTIME* and FLAPPING* types are dropped by PagerDuty's API;
        # this means we can unintentionally miss when alerts resolve if they
        # resolve during either of these states.  If we coerce these events
        # to the PROBLEM/RECOVERY types, and enable notifications for
        # DOWNTIME/FLAPPING events, then we won't miss them.
        notification_type = event["NOTIFICATIONTYPE"]
        if any(
            notification_type.startswith(prefix) for prefix in ("FLAPPING", "DOWNTIME")
        ):
            service_state = event["SERVICESTATE"]
            if service_state == "OK":
                new_type = "RECOVERY"
            elif service_state == "CRITICAL":
                new_type = "PROBLEM"
            else:
                # For now; treat all other cases as problems as well
                new_type = "PROBLEM"
            event["COERCEDFROMTYPE"] = notification_type
            event["NOTIFICATIONTYPE"] = new_type

    event_file = os.path.join(
        args.queue_dir, "pd_{}_{}.txt".format(int(time.time()), os.getpid())
    )
    with open(event_file, "w") as outfile:
        for key, value in event.items():
            outfile.write("{}={}\n".format(key, value))


def lock_and_flush_queue(args):
    lockfile = os.path.join(args.queue_dir, "lockfile")
    with open(lockfile, "w") as outfile:
        fcntl.flock(outfile.fileno(), fcntl.LOCK_EX)
        return flush_queue(args)


def flush_queue(args):
    queue = get_queue_from_dir(args)
    for file in queue:
        path = os.path.join(args.queue_dir, file)
        if args.verbose:
            print("=== Now processing: {}".format(path))
        with open(path) as infile:
            event = {}
            for line in infile:
                key, value = line.strip().split("=", 1)
                event[key] = value
        request = Request(
            args.api_base + "/create_event", data=urlencode(event).encode()
        )
        try:
            response = urlopen(request, timeout=TIMEOUT_S)
        except HTTPError as e:
            if 400 <= e.code < 500:
                # Client error
                content = e.fp.read().decode()
                logging.warning(
                    "Nagios event in file %s REJECTED by the PagerDuty server.  Server says: %s",
                    path,
                    content,
                )
                if "retry later" not in content:
                    os.unlink(path)
            else:
                logging.warning(
                    "Nagios event in file %s DEFERRED due to network/server problems.",
                    path,
                )
                return False
        else:
            # Success
            logging.info(
                "Nagios event in file %s ACCEPTED by the PagerDuty server.", path
            )
            os.unlink(path)
    # Everything that was intended to be sent, was sent and handled in some way.
    # (Minus stuff needing a retry.)
    return True


def get_queue_from_dir(args):
    timestamp_file_pairs = []
    for file in os.listdir(args.queue_dir):
        match = re.match("^pd_(\d+)_\d+.txt$", file)
        if match:
            timestamp = match.group(1)
            timestamp_file_pairs.append((timestamp, file))
    timestamp_file_pairs.sort(key=lambda x: x[0])
    return [t[1] for t in timestamp_file_pairs]


if __name__ == "__main__":
    main()
