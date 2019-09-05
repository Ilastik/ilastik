#!/usr/bin/env python

###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2014, the ilastik developers
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
#          http://ilastik.org/license.html
###############################################################################

import os
import pathlib
import shlex
import sys
from typing import Iterable, Mapping, Sequence, Tuple, Union


def _env_list(name: str, sep: str = os.pathsep) -> Iterable[str]:
    """Items from the environment variable, delimited by separator.

    Empty sequence if the variable is not set.
    """
    value = os.environ.get(name, "")
    if not value:
        return []
    return value.split(sep)


def _clean_paths(root: pathlib.Path) -> None:
    def issubdir(path):
        """Whether path is equal to or is a subdirectory of root."""
        path = pathlib.PurePath(path)
        return path == root or any(parent == root for parent in path.parents)

    def subdirs(*suffixes):
        """Valid subdirectories of root."""
        paths = map(root.joinpath, suffixes)
        return [str(p) for p in paths if p.is_dir()]

    def isvalidpath_win(path):
        """Whether an element of PATH is "clean" on Windows."""
        patterns = "*/cplex/*", "*/guirobi/*", "/windows/system32/*"
        return any(map(pathlib.PurePath(path).match, patterns))

    # Remove undesired paths from PYTHONPATH and add ilastik's submodules.
    sys_path = list(filter(issubdir, sys.path))
    sys_path += subdirs("ilastik/lazyflow", "ilastik/volumina", "ilastik/ilastik")
    sys.path = sys_path

    if sys.platform.startswith("win"):
        # Empty PATH except for gurobi and CPLEX and add ilastik's installation paths.
        path = list(filter(isvalidpath_win, _env_list("PATH")))
        path += subdirs("Qt4/bin", "Library/bin", "python", "bin")
        os.environ["PATH"] = os.pathsep.join(reversed(path))
    else:
        # Clean LD_LIBRARY_PATH and add ilastik's installation paths
        # (gurobi and CPLEX are supposed to be located there as well).
        ld_lib_path = list(filter(issubdir, _env_list("LD_LIBRARY_PATH")))
        ld_lib_path += subdirs("lib")
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(reversed(ld_lib_path))


def _parse_internal_config(path: Union[str, os.PathLike]) -> Tuple[Sequence[str], Mapping[str, str]]:
    """Parse options from the internal config file.

    Args:
        path: Path to the config file.

    Returns:
        Additional command-line options and environment variable assignments.
        Both are empty if the config file does not exist.

    Raises:
        ValueError: Config file is malformed.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return [], {}

    opts = shlex.split(path.read_text(), comments=True)

    sep_idx = tuple(i for i, opt in enumerate(opts) if opt.startswith(";"))
    if len(sep_idx) != 1:
        raise ValueError(f"{path} should have one and only one semicolon separator")
    sep_idx = sep_idx[0]

    env_vars = {}
    for opt in opts[:sep_idx]:
        name, sep, value = opt.partition("=")
        if not name or not sep:
            raise ValueError(f"invalid environment variable assignment {opt!r}")
        env_vars[name] = value

    return opts[sep_idx + 1 :], env_vars


def main():
    if "--clean_paths" in sys.argv:
        script_dir = pathlib.Path(__file__).parent
        ilastik_root = script_dir.parent.parent
        _clean_paths(ilastik_root)

    # Allow to start-up by double-clicking a project file.
    if len(sys.argv) == 2 and sys.argv[1].endswith(".ilp"):
        sys.argv.insert(1, "--project")

    arg_opts, env_vars = _parse_internal_config("internal-startup-options.cfg")
    sys.argv[1:1] = arg_opts
    os.environ.update(env_vars)

    import ilastik_main

    parsed_args, workflow_cmdline_args = ilastik_main.parse_known_args()

    hShell = ilastik_main.main(parsed_args, workflow_cmdline_args)
    # in headless mode the headless shell is returned and its project manager still has an open project file
    hShell.closeCurrentProject()


if __name__ == "__main__":
    main()
