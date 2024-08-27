###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2024, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# In addition, as a special exception, the copyright holders of
# ilastik give you permission to combine ilastik with applets,
# workflows and plugins which are not covered under the GNU
# General Public License.
#
# See the LICENSE file for details. License information is also available
# on the ilastik web site at:
# 		   http://ilastik.org/license.html
###############################################################################

import configparser
import logging
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, List, Mapping, Optional, Union

import appdirs
from annotated_types import Ge, Le
from pydantic import AfterValidator, BaseModel, Field, PlainSerializer, ConfigDict, model_validator

logger = logging.getLogger(__name__)

"""
ilastik will read settings from ilastik.ini
which should be located at

* windows: C:\\Users\\<USERNAME>\\AppData\\Local\\ilastik
* osx: /Users/<USERNAME>/Library/Caches/ilastik
* linux: /home/<USERNAME>/.config/ilastik

Example:

[ilastik]
debug: false
plugin_directories: ~/.ilastik/plugins,
logging_config: ~/custom_ilastik_logging_config.json
"""


@dataclass
class Increment:
    increment: int


class _ConfigBase(BaseModel):
    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class IlastikSection(_ConfigBase):

    plugin_directories: str = "~/.ilastik/plugins,"
    """Comma-separated list of paths to search for Object Classification / Tracking Feature plugins."""

    output_filename_format: str = "{dataset_dir}/{nickname}_{result_type}"
    """Default export filename, supports magic placeholders."""

    output_format: str = "compressed hdf5"
    """Default export format - consult documentation for allowed values."""

    logging_config: str = ""
    """Json file with logging configuration."""

    debug: Annotated[
        bool,
        AfterValidator(bool),
        PlainSerializer(str, return_type=str, when_used="always"),
    ] = Field(default=False)
    """Enable debug mode (for developers only)."""

    hbp: Annotated[
        bool,
        AfterValidator(bool),
        PlainSerializer(str, return_type=str, when_used="always"),
    ] = Field(default=False)
    """Enable legacy hbp mode. If checked, the VoxelSegmentationWorkflow will be visible."""


class LazyflowSection(_ConfigBase):
    threads: Annotated[
        int,
        Ge(-1),
        Le(4000),
        Increment(1),
    ] = Field(default=-1)
    """
        Set the number of threads for ilastik. -1 means that ilastik determines number of threads automatically,
        0 will run synchronously (for dev only).
    """
    total_ram_mb: Annotated[
        int,
        Ge(0),
        Le(1024 * 8000),
        Increment(100),
    ] = Field(default=0)
    """Limit amount of RAM (in MB) for ilastik. 0 means no-limit."""


class IlastikPreferences(_ConfigBase):
    ilastik: IlastikSection = IlastikSection()
    lazyflow: LazyflowSection = LazyflowSection()


default_config = """
[ilastik]
debug: false
plugin_directories: ~/.ilastik/plugins,
output_filename_format: {dataset_dir}/{nickname}_{result_type}
output_format: compressed hdf5

[lazyflow]
threads: -1
total_ram_mb: 0
"""


@dataclass
class RuntimeCfg:
    tiktorch_executable: Optional[List[str]] = None
    preferred_cuda_device_id: Optional[str] = None


cfg: IlastikPreferences = IlastikPreferences()
cfg_path: Optional[Path] = None
runtime_cfg: RuntimeCfg = RuntimeCfg()


def _init(path: Union[str, bytes, os.PathLike]) -> None:
    """Initialize module variables."""
    config_path = Path(path)

    if config_path.is_file():
        logger.info(f"Loading configuration from {config_path!s}.")
        _cfg = configparser.ConfigParser()
        _cfg.read_string(config_path.read_text())

        flat_dict = {}
        for k, v in _cfg.items():
            if not isinstance(v, Mapping):
                flat_dict[k] = v
            else:
                flat_dict[k] = {sk: sv for sk, sv in v.items()}

        config = IlastikPreferences.model_validate(flat_dict)
    else:
        config = IlastikPreferences()

    global cfg, cfg_path
    cfg, cfg_path = config, config_path


def _get_default_config_path() -> Path:
    """Return a default, valid config path, or None if none of the default paths are valid."""
    old = Path.home() / ".ilastikrc"
    new = Path(appdirs.user_config_dir(appname="ilastik", appauthor=False)) / "ilastik.ini"

    if old.is_file():
        warnings.warn(
            f"ilastik config file location {str(old)!r} is deprecated; "
            f"move config file to the new location {str(new)!r}",
            DeprecationWarning,
        )
        return old
    else:
        return new


def init_ilastik_config(path: Union[None, str, bytes, os.PathLike] = None) -> None:
    if path is None:
        _init(_get_default_config_path())
    elif os.path.isfile(path):
        _init(path)
    else:
        raise RuntimeError(f"ilastik config file {path} does not exist or is not a file")


init_ilastik_config()


if __name__ == "__main__":

    print(cfg)
