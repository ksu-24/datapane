import dataclasses as dc
import typing as t
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Dict, Optional

import click
import dacite
import yaml
from furl import furl

from datapane import _IN_PYTEST, log

from .utils import InvalidTokenError

APP_NAME = "datapane"
APP_DIR = Path(click.get_app_dir(APP_NAME))
APP_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_ENV = "default"
DEFAULT_SERVER = "https://datapane.com"
DEFAULT_TOKEN = "TOKEN_HERE"


# TODO - don't create configfile until user logs in, and delete rather than reset on logout
def get_default_config() -> str:
    """The default structure for the config file"""
    return f"""\
server: {DEFAULT_SERVER}
token: {DEFAULT_TOKEN}
username: ''
session_id: {uuid.uuid4().hex}
# set to false to not send analytics data
analytics: true
"""


def get_config_file(env: str = DEFAULT_ENV, reset: bool = False) -> Path:
    config_f = APP_DIR / f"{env}.yaml"
    if reset or not config_f.exists():
        config_f.write_text(get_default_config())
        log.debug(f"Creating default config file at {config_f}")
    return config_f


@dc.dataclass(frozen=True)
class Config:
    """Global config read from config file"""

    # TODO - hardcode to datapane.com for now
    server: str
    token: str
    username: str = ""
    session_id: str = ""
    analytics: bool = True


def default_analytics_state(server: str, old_state: bool = True) -> bool:
    from .api import by_datapane

    if old_state is False or by_datapane:
        return False
    # Temp: Remove this after testing
    if "localhost" in server:
        return True
    f = furl(server)
    return "datapane" == f.host.split(".")[-2]

    @property
    def is_public(self) -> bool:
        return self.server == DEFAULT_SERVER


# TODO - wrap into a singleton object that includes callable?
config: Optional[Config] = None
last_config_env: Optional[str] = None


def set_config(c: Optional[Config]):
    global config
    config = c


def get_config() -> Config:
    global config
    return config


def is_public() -> bool:
    return get_config().server == DEFAULT_SERVER


def check_get_config() -> Config:
    """Attempt to get a config object, reloading if necessary"""
    global config
    if config and config.token == DEFAULT_TOKEN:
        # try reinit, as may have ran login in another terminal/subprocess
        global last_config_env
        init(last_config_env)
        if config.token == DEFAULT_TOKEN:
            # still don't have a token set for the env, open up the browser
            if not _IN_PYTEST:
                f = furl(path="/home/", origin=config.server)
                webbrowser.open(url=str(f), new=2)
            raise InvalidTokenError(
                "Please sign-up and login - if you already have then please restart your Jupyter kernel/Python instance to initialize your new token"
            )
    return config


@contextmanager
def update_config(config_env: str) -> ContextManager[Dict]:
    """Update config file and reinit in-memory config"""
    config_f = get_config_file(config_env)

    with config_f.open("r") as f:
        code = yaml.safe_load(f)

    yield code

    with config_f.open("w") as f:
        yaml.safe_dump(code, f)

    # reinit if already in process
    init(config_env=config_env)


def load_from_envfile(config_env: str) -> Path:
    """Init the cmd-line env"""
    global last_config_env
    last_config_env = config_env
    config_f = get_config_file(config_env)

    with config_f.open("r") as f:
        c_yaml = yaml.safe_load(f)
    # load config obj from file
    c_obj = dacite.from_dict(Config, c_yaml)
    # log.debug(f"Read config as {c_obj}")
    set_config(c_obj)

    return config_f


def update_config_with_analytics():
    from .api import ping

    config = get_config()
    # if the config is old(i,e: does not have session_id), update it.
    if config.session_id == "":
        with update_config(last_config_env) as x:
            x["session_id"] = uuid.uuid4().hex
            x["analytics"] = default_analytics_state(config.server)

        # If the user was already logged in call ping to generate alias on the server
        if config.username:
            ping(cli_login=True)


def init(config_env: str = "default", config: t.Optional[Config] = None):
    """Init an API config - this MUST handle being called multiple times"""
    from .analytics import capture_init

    if get_config() is not None:
        log.debug("Reinitialising client config")

    if config:
        set_config(config)
    else:
        config_f = load_from_envfile(config_env)
        log.debug(f"Loaded client environment from {config_f}")
    # TODO: find a better place for this
    update_config_with_analytics()
    capture_init()
