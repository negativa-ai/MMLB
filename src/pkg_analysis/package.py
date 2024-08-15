from abc import ABC
import subprocess


class Package(ABC):
    pass


class PipPackage(Package):
    def __init__(self, name, version=None, desc=None, size=-1):
        self.name = name
        self.version = version
        self.desc = desc
        self.size = size  # kb
        self.type = "pip"
        self.files = []
        self.location = ""
        self.occupied_size = 0

    def __str__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.size) + "kb"

    def __repr__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.size) + "kb"


class AptPackage(Package):
    def __init__(self, name, version=None, desc=None, installed_size=-1):
        self.name = name
        self.version = version
        self.desc = desc
        # derived from deb control file: https://www.debian.org/doc/manuals/debian-faq/pkg-basics.en.html
        # dosen't have to be the same as the real size
        self.installed_size = installed_size
        self.type = "apt"
        self.occupied_size = 0

    def __str__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.installed_size) + "kb"

    def __repr__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.installed_size) + "kb"



class CondaPackage(Package):
    def __init__(self, name, version=None, desc=None, size=-1):
        self.name = name
        self.version = version
        self.desc = desc
        self.size = size  # kb
        self.type = "conda"
        self.files = []
        self.location = ""
        self.occupied_size = 0

    def __str__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.size) + "kb"

    def __repr__(self) -> str:
        return self.name + ":" + self.version + ":" + str(self.size) + "kb"


class PkgFile(object):
    def __init__(self, name, size=0) -> None:
        self.name = name
        self.size = size

    def __str__(self) -> str:
        return self.name + ":" + str(self.size) + "kb"

    def __repr__(self) -> str:
        return self.name + ":" + str(self.size) + "kb"

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, __o: object) -> bool:
        return self.__class__ == __o.__class__ and self.name == __o.name
