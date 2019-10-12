# Overview

[Nagios](http://nagios.org) offers complete monitoring and alerting for servers, switches, applications, and services.

This charm is designed to do basic monitoring of any service in the Charm Store that relates to it. There is an [NRPE subordinate charm](https://jujucharms.com/nrpe/) that you can use if you want to use local monitors.

# Usage

This charm is designed to be used with other charms. In order to monitor anything in your juju environment for working PING and SSH, just relate the services to this service. In this example we deploy a central monitoring instance, mediawiki, a database, and then monitor them with Nagios:

    juju deploy nagios central-monitor
    juju deploy mysql big-db
    juju deploy mediawiki big-wiki
    juju add-relation big-db:db big-wiki:db
    juju add-relation big-db central-monitor
    juju add-relation big-wiki central-monitor

This should result in your Nagios monitoring all of the service units.

There is an [NRPE subordinate charm](https://jujucharms.com/nrpe/) which must be used for any local monitors.  See the `nrpe` charm's README for information on how to make use of it.

You can expose the service and browse to `http://x.x.x.x/nagios3` to get to the web UI, following the example:

    juju expose central-monitor
    juju status central-monitor

Will get you the public IP of the web interface.

# Livestatus Configuration

- `enable_livestatus` - Setting to enable the [livestatus module](https://mathias-kettner.de/checkmk_livestatus.html). This is an easy interface to get data out of Nagios.

- `livestatus_path` - Configuration of where the livestatus module is stored - defaults to /var/lib/nagios3/livestatus/socket.

- `livestatus_args` - Arguments to be passed to the livestatus module, defaults to empty.

# Pagerduty Configuration

- `enable_pagerduty` - Config variable to enable pagerduty notifications or not.

- `pagerduty_key` - Pagerduty API key to use for notifications

- `pagerduty_path` - Path for Pagerduty notifications to be queued, default is /var/lib/nagios3/pagerduty.

# Configuration

- `nagios_user` - The effective user that nagios will run as.

- `nagios_group` - The effective group that nagios will run as.

- `check_external_commands` - Config variable to enable checking external commands.

- `command_check_interval` - How often to check for external commands.

- `command_file` - File that Nagios checks for external command requests.

- `debug_level` - Specify the debug level for nagios.  See the docs for more details.

- `debug_verbosity` - How verbose will the debug logs be - 0 is brief, 1 is more detailed and 2 is very detailed.

- `debug_file` - Path for the debug file - defaults to /var/log/nagios3/nagios.debug.

- `daemon_dumps_core` - Option to determine if Nagios is allowed to create a core dump.

- `admin_email` - Email address used for the admin, used by $ADMINEMAIL$ in notification commands - defaults to root@localhost.

- `admin_pager` - Email address used for the admin pager, used by $ADMINPAGER$ in notification commands - defaults to pageroot@localhost.

- `log_rotation_method` - Log rotation method that Nagios should use to rotate the main logfile, defaults to "d".

- `log_archive_path` - Path for archived log files, defaults to /var/log/nagios3/archives
- `use_syslog` - Log messages to syslog as well as main file.

- `password` - Password to use for administrative access instead of a generated password.

- `extra_contacts` - List of extra administrator contacts to configure. Useful for integrating with external notification services (e.g. Slack, RocketChat)

### SSL Configuration

- `ssl` - Determinant configuration for enabling SSL. Valid options are "on", "off", "only". The "only" option disables HTTP traffic on Apache in favor of HTTPS. This setting may cause unexpected behavior with existing nagios charm deployments.

- `ssl_cert` - Base64 encoded SSL certificate. Deploys to configured ssl_domain certificate name as `/etc/ssl/certs/{ssl_domain}.pem`.   If left blank, the certificate and key will be autogenerated as self-signed.

- `ssl_key` - Base64 encoded SSL key. Deploys to configured ssl_domain key as `/etc/ssl/private/{ssl_domain}.key`.  If `ssl_cert` is blank, this option will be ignored.

- `ssl_chain` - Base64 encoded SSL Chain. Deploys to configured ssl_domain chain authority as `/etc/ssl/certs/{ssl_domain}.csr`.  If `ssl_cert` is blank, this option will be ignored.


#### Typical SSL Workflow for Self Signed Keys:

    juju deply nagios central-monitor
    juju set central-monitor ssl=on


If you purchased keys from a certificate authority:

    juju deply nagios central-monitor
    juju set central-monitor ssl_cert=`base64 mykey.pem`
    juju set central-monitor ssl_key=`base64 mykey.key`
    juju set central-monitor ssl_chain=`base64 mykey.csr`
    juju set central-monitor ssl=on


### Known Issues / Caveates


#### Web Interface username/password

Login: nagiosadmin
Password: see below

To fetch the Nagios Administrative password you have to retrieve them from
the nagios host, as it is generated during installation.

    juju ssh central-monitor/0 sudo cat /var/lib/juju/nagios.passwd

#### Monitors Interface
The monitors interface expects three fields:

- `monitors` - YAML matching the monitors yaml spec. See example.monitors.yaml for more information.
- `target-id` - Assign any monitors to this target host definition.
- `target-address` - Optional, specifies the host of the target to monitor. This must be specified by at least one unit so that the intended target-id will be monitorable.


# Contact Information

## Nagios

- [Nagios homepage](http://nagios.org)
- [Nagios documentation](http://www.nagios.org/documentation)
- [Nagios support](http://www.nagios.org/support)
