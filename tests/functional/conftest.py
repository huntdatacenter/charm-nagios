#!/usr/bin/python3

import asyncio
import json
import os
import uuid

import juju
from juju.controller import Controller
from juju.errors import JujuError
from juju.model import Model

import pytest

STAT_FILE = "python3 -c \"import json; import os; s=os.stat('%s'); print(json.dumps({'uid': s.st_uid, 'gid': s.st_gid, 'mode': oct(s.st_mode), 'size': s.st_size}))\""  # noqa: E501


@pytest.yield_fixture(scope="session")
def event_loop(request):
    """Override the default pytest event loop to allow for broaded scopedv fixtures."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_debug(True)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture(scope="session")
async def controller():
    """Connect to the current controller."""
    controller = Controller()
    await controller.connect_current()
    yield controller
    await controller.disconnect()


# Comment for re-using the current model
@pytest.fixture(scope="session")
async def model(controller):
    """Create a model that lives only for the duration of the test."""
    model_name = "functest-{}".format(uuid.uuid4())
    model = await controller.add_model(model_name)
    yield model
    await model.disconnect()

    if os.getenv("PYTEST_KEEP_MODEL"):
        return
    await controller.destroy_model(model_name)

    while model_name in await controller.list_models():
        await asyncio.sleep(1)


# Uncomment for re-using the current model, useful for debugging functional tests
# @pytest.fixture(scope='session')
# async def model():
#     model = Model()
#     await model.connect_current()
#     yield model
#     await model.disconnect()


@pytest.fixture(scope="session")
async def current_model():
    """Return the current model, does not create or destroy it."""
    model = Model()
    await model.connect_current()
    yield model
    await model.disconnect()


@pytest.fixture
async def get_app(model):
    """Return the application requested."""  # noqa D202

    async def _get_app(name):
        try:
            return model.applications[name]
        except KeyError:
            raise JujuError("Cannot find application {}".format(name))

    return _get_app


@pytest.fixture
async def get_unit(model):
    """Return the requested <app_name>/<unit_number> unit."""  # noqa D202

    async def _get_unit(name):
        try:
            (app_name, unit_number) = name.split("/")

            return model.applications[app_name].units[unit_number]
        except (KeyError, ValueError):
            raise JujuError("Cannot find unit {}".format(name))

    return _get_unit


@pytest.fixture
async def get_entity(model, get_unit, get_app):
    """Return a unit or an application."""  # noqa D202

    async def _get_entity(name):
        try:
            return await get_unit(name)
        except JujuError:
            try:
                return await get_app(name)
            except JujuError:
                raise JujuError("Cannot find entity {}".format(name))

    return _get_entity


@pytest.fixture
async def run_command(get_unit):
    """Run a command on a unit."""  # noqa D202

    async def _run_command(cmd, target):
        """
        Run a command on a unit.

        :param cmd: Command to be run
        :param target: Unit object or unit name string
        """
        unit = target if type(target) is juju.unit.Unit else await get_unit(target)
        action = await unit.run(cmd)

        return action.results

    return _run_command


@pytest.fixture
async def file_stat(run_command):
    """Run stat on a file.

    :param path: File path
    :param target: Unit object or unit name string
    """  # noqa D202

    async def _file_stat(path, target):
        cmd = STAT_FILE % path
        results = await run_command(cmd, target)

        return json.loads(results["Stdout"])

    return _file_stat


@pytest.fixture
async def file_contents(run_command):
    """Return the contents of a file."""  # noqa D202

    async def _file_contents(path, target):
        """Return the contents of a file.

        :param path: File path
        :param target: Unit object or unit name string
        """
        cmd = "cat {}".format(path)
        results = await run_command(cmd, target)

        return results.get("Stdout")

    return _file_contents


@pytest.fixture
async def reconfigure_app(get_app, model):
    """Apply a different config to the requested app."""  # noqa D202

    async def _reconfigure_app(cfg, target):
        application = (
            target
            if type(target) is juju.application.Application
            else await get_app(target)
        )
        await application.set_config(cfg)
        await application.get_config()
        await model.block_until(lambda: application.status == "active")

    return _reconfigure_app


@pytest.fixture
async def create_group(run_command):
    """Create the UNIX group specified."""  # noqa D202

    async def _create_group(group_name, target):
        cmd = "sudo groupadd %s" % group_name
        await run_command(cmd, target)

    return _create_group


pytestmark = pytest.mark.asyncio

PROJECTPATH = os.getenv("PROJECTPATH").rstrip("/")

SERIES = ["xenial", "bionic"]


############
# FIXTURES #
############
@pytest.fixture(scope="session", params=SERIES)
def series(request):
    """Return ubuntu version (i.e. xenial) in use in the test."""
    return request.param


@pytest.fixture(scope="session")
async def relatives(model, series):
    nrpe = "nrpe"
    nrpe_name = "nrpe-{}".format(series)
    nrpe_app = await model.deploy(
        "cs:" + nrpe, application_name=nrpe_name, series=series, config={}, num_units=0
    )

    mysql = "mysql"

    if series != "trusty":
        mysql = "percona-cluster"

    mysql_name = "mysql-{}".format(series)
    mysql_app = await model.deploy(
        "cs:" + mysql, application_name=mysql_name, series=series, config={}
    )

    await model.add_relation(
        "{}:nrpe-external-master".format(mysql_name),
        "{}:nrpe-external-master".format(nrpe_name),
    )
    await model.block_until(
        lambda: mysql_app.units[0].workload_status == "active"
        and mysql_app.units[0].agent_status == "idle"  # noqa W503
    )

    yield {
        "mysql": {"name": mysql_name, "app": mysql_app},
        "nrpe": {"name": nrpe_name, "app": nrpe_app},
    }


@pytest.fixture(scope="session")
async def deploy_app(relatives, model, series):
    """Return application of the charm under test."""
    app_name = "nagios-{}".format(series)

    """Deploy the nagios app."""
    nagios_app = await model.deploy(
        os.path.join(PROJECTPATH, "nagios.charm"),
        application_name=app_name,
        series=series,
        config={
            "enable_livestatus": False,
            "ssl": "off",
            "extraconfig": "",
            "enable_pagerduty": False,
        },
    )

    await model.add_relation(
        "{}:monitors".format(relatives["nrpe"]["name"]), "{}:monitors".format(app_name)
    )
    await model.block_until(
        lambda: nagios_app.units[0].agent_status == "idle"
        and relatives["mysql"]["app"].units[0].agent_status == "idle"  # noqa W503
    )

    yield nagios_app

    if os.getenv("PYTEST_KEEP_MODEL"):
        return

    for relative in list(relatives.values()):
        app = relative["app"]
        await app.destroy()
    await nagios_app.destroy()


class Agent:
    def __init__(self, unit, application):
        self.u = unit
        self.application = application
        self.model = unit.model

    def is_active(self, status):
        u = self.u

        return u.agent_status == status and u.workload_status == "active"

    async def block_until_or_timeout(self, lambda_f, **kwargs):
        await self.block_until(lambda_f, ignore_timeout=True, **kwargs)

    async def block_until(
        self, lambda_f, timeout=120, wait_period=5, ignore_timeout=False
    ):
        try:
            await self.model.block_until(
                lambda_f, timeout=timeout, wait_period=wait_period
            )
        except asyncio.TimeoutError:
            if not ignore_timeout:
                raise

    @property
    async def public_address(self):
        return await self.u.get_public_address()


@pytest.fixture()
async def unit(model, deploy_app):
    """Return the unit we've deployed."""
    unit = Agent(deploy_app.units[0], deploy_app)
    await unit.block_until(lambda: unit.is_active("idle"))

    return unit


@pytest.fixture()
async def auth(file_contents, unit):
    """Return the basic auth credentials."""
    nagiospwd = await file_contents("/var/lib/juju/nagios.passwd", unit.u)

    return "nagiosadmin", nagiospwd.strip()
