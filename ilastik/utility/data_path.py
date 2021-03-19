from abc import ABC, abstractmethod
from typing import TypeVar, Sequence, Union, List
from pathlib import PurePosixPath, Path
import errno
import glob
import os
import re

import numpy as np
import z5py
import h5py
import z5py

from lazyflow.utility.pathHelpers import splitPath, globH5N5, globNpz


DP = TypeVar("DP", bound="DataPath")


class DataPath(ABC):
    def __init__(self, raw_path: str):
        self.raw_path = raw_path

    @staticmethod
    def create(path: str) -> "DataPath":
        try:
            return ArchiveDataPath.create(path)
        except ValueError:
            return SimpleDataPath(path)

    @abstractmethod
    def exists(self) -> bool:
        pass

    @abstractmethod
    def relative_to(self: DP, other: Path) -> DP:
        pass

    @abstractmethod
    def glob(self, smart: bool = True) -> Sequence["DataPath"]:
        pass


class SimpleDataPath(DataPath):
    def __init__(self, raw_path: str):
        super().__init__(raw_path=raw_path)
        self.path = Path(raw_path)

    def exists(self) -> bool:
        return self.path.exists()

    def relative_to(self, other: Path) -> "SimpleDataPath":
        return SimpleDataPath(str(self.path.relative_to(other)))

    def glob(self, smart: bool = True) -> Sequence["DataPath"]:
        if smart and self.exists():
            return [self]
        expanded_paths = [DataPath.create(p) for p in glob.glob(str(self.path))]
        if not expanded_paths:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(self.path))
        return expanded_paths


class ArchiveDataPath(DataPath):
    def __init__(self, external_path: Path, internal_path: PurePosixPath):
        if external_path.suffix.lower() not in self.suffixes():
            raise ValueError(f"External path for {self.__class__.__name__} must end in {self.suffixes()}")
        self.external_path = external_path
        self.internal_path = PurePosixPath("/") / internal_path
        super().__init__(str(external_path / internal_path.relative_to("/")))

    @staticmethod
    def get_suffixes() -> Sequence[str]:
        return [suffix for klass in ArchiveDataPath.__subclasses__() for suffix in klass.suffixes()]

    @staticmethod
    def create(path: str) -> "ArchiveDataPath":
        archive_suffix_regex = r"\.(" + "|".join(ArchiveDataPath.get_suffixes()) + ")(?:$|/)"
        components = re.split(archive_suffix_regex, str(path), maxsplit=1, flags=re.IGNORECASE)
        if len(components) != 3:
            raise ValueError(f"Path '{path}' does not look like an archive path")
        external_path = Path(components[0] + components[1])
        internal_path = PurePosixPath("/") / components[2]

        if internal_path == PurePosixPath("/"):
            raise ValueError(f"Path to archive file has empty path: '{str(external_path) + str(internal_path)}'")
        external_suffix = external_path.suffix.lower()[1:]
        if external_suffix in H5DataPath.suffixes():
            return H5DataPath(external_path=external_path, internal_path=internal_path)
        if external_suffix in N5DataPath.suffixes():
            return N5DataPath(external_path=external_path, internal_path=internal_path)
        if external_suffix in NpzDataPath.suffixes():
            return NpzDataPath(external_path=external_path, internal_path=internal_path)
        # this should never happen
        raise ValueError(f"Unexpected archive suffix in '{str(external_path) + str(internal_path)}'")

    @abstractmethod
    def glob_internal(self: DP, smart: bool = True) -> List[DP]:
        pass

    @classmethod
    @abstractmethod
    def suffixes(cls) -> Sequence[str]:
        pass

    def relative_to(self, other: Path) -> "DataPath":
        return self.__class__(self.external_path.relative_to(other), self.internal_path)

    def __lt__(self, other: "DataPath") -> bool:
        if isinstance(other, ArchiveDataPath):
            return (str(self.external_path), str(self.internal_path)) < (
                str(other.external_path),
                str(other.internal_path),
            )
        else:
            return self.raw_path < other.raw_path

    def glob(self, smart: bool = True) -> Sequence["ArchiveDataPath"]:
        if smart and self.external_path.exists():
            externally_expanded_paths = [self]
        else:
            externally_expanded_paths = [
                self.__class__(external_path=Path(ep), internal_path=self.internal_path)
                for ep in glob.glob(str(self.external_path))
            ]
            if not externally_expanded_paths:
                raise ValueError(f"Pattern {self.external_path} expands to nothing")

        all_paths: List["ArchiveDataPath"] = []
        for data_path in sorted(externally_expanded_paths):
            all_paths += [data_path] if (smart and data_path.exists()) else sorted(data_path.glob_internal(smart))
        return all_paths


class H5DataPath(ArchiveDataPath):
    @classmethod
    def suffixes(cls) -> Sequence[str]:
        return ["h5", "hdf5", "ilp"]

    def glob_internal(self) -> Sequence["H5DataPath"]:
        with h5py.File(str(self.external_path), "r") as f:
            return [
                H5DataPath(self.external_path, internal_path=PurePosixPath(p))
                for p in globH5N5(f, str(self.internal_path).lstrip("/"))
            ]

    def exists(self) -> bool:
        if not self.external_path.exists():
            return False
        with h5py.File(str(self.external_path), "r") as f:
            return self.internal_path.as_posix() in f


class N5DataPath(ArchiveDataPath):
    @classmethod
    def suffixes(cls) -> Sequence[str]:
        return ["n5"]

    def glob_internal(self) -> Sequence["N5DataPath"]:
        with z5py.N5File(str(self.external_path)) as f:
            return [
                N5DataPath(self.external_path, internal_path=PurePosixPath(p))
                for p in globH5N5(f, str(self.internal_path).lstrip("/"))
            ]

    def exists(self) -> bool:
        if not self.external_path.exists():
            return False
        with z5py.N5File(str(self.external_path)) as f:
            return self.internal_path.as_posix() in f


class NpzDataPath(ArchiveDataPath):
    @classmethod
    def suffixes(cls) -> Sequence[str]:
        return ["npz"]

    def glob_internal(self) -> Sequence["NpzDataPath"]:
        return [
            NpzDataPath(self.external_path, internal_path=PurePosixPath(p))
            for p in globNpz(str(self.external_path), str(self.internal_path).lstrip("/"))
        ]

    def exists(self) -> bool:
        if not self.external_path.exists():
            return False
        return self.internal_path.as_posix().lstrip("/") in np.load("mnist.npz", mmap_mode="r").files


class DatasetPath:
    def __init__(self, data_paths: Sequence[DataPath]):
        self.data_paths = data_paths

    @classmethod
    def from_string(cls, path_str: str, smart: bool = True) -> "DatasetPath":
        if not smart or Path(path_str).exists():
            return DatasetPath([DataPath.create(path_str)])

        data_paths = [DataPath.create(p) for p in path_str.split(os.path.pathsep)]
        out: List[DataPath] = []
        for data_path in data_paths:
            out += [data_path] if data_path.exists() else data_path.glob(smart=smart)
        return DatasetPath(out)
