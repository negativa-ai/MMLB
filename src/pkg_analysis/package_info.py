from abc import ABC, abstractmethod
import json
import os

import docker


class PkgInfoFiller(ABC):
    @abstractmethod
    def fit(self, pkgs):
        """
        Args:
            pkgs: list of Package

        Returns:
            None

        Fit metadata of packages
        """
        raise NotImplementedError()


class AptPkgInfoFiller(PkgInfoFiller):
    def __init__(self, container):
        self.container = container
        self.client = docker.from_env()

    def _show_info(self, pkgs):
        cmd = "apt show "
        for p in pkgs:
            cmd = cmd + p.name + " "
        output = (
            self.client.containers.run(self.container, cmd, remove=True, entrypoint="")
            .decode("utf-8")
            .split("\n\n")
        )

        return output

    def _parse_info(self, output):
        """
        Args:
            output: a str list like
                ['Package: apt
                Version: 2.4.6
                Installed-Size: 4051 kB
                Description: commandline package manager
                This package provides commandline tools for searching and
                managing as well as querying information about packages
                as a low-level access to all features of the libapt-pkg library.
                .
                These include:
                * apt-get for retrieval of packages and information about them
                    from authenticated sources and for installation, upgrade and
                    removal of packages together with their dependencies',
                'Package: adduser
                Version: 3.118ubuntu5
                Installed-Size: 623 kB
                Description: add and remove users and groups
                This package includes the 'adduser' and 'deluser' commands for creating
                and removing users.
                .
                - 'adduser' creates new users and groups and adds existing users to
                    existing groups;']

        """
        # filter irrelevant msgs
        # count = 0
        # for i, s in enumerate(output):
        #     if s.startswith('Package: '):
        #         count = i
        #         break
        # output = output[count:]

        infos = []
        for pkg_info in output:
            pkg_info = pkg_info.strip().splitlines()
            desc = None
            size = None
            pkg = None  # for checking
            for line in pkg_info:
                if desc != None and size != None and pkg != None:
                    break
                s = line.split(":")

                tag = s[0].strip()
                if tag == "Installed-Size":
                    value = s[1].strip()
                    if value != "unknown":
                        value = value.replace(",", "")
                        size = round(float(value.split(" ")[0].strip()), 2)

                elif tag == "Description":
                    desc = s[1].strip()
                elif tag == "Package":
                    pkg = s[1].strip()
            infos.append((pkg, desc, size))

        return infos

    def _fit_one_pkg(self, pkg):
        output = self._show_info(pkg.name)
        desc, size = self._parse_info(output)
        pkg.desc = desc
        pkg.size = size

    def fit(self, pkgs):
        if len(pkgs) == 0:
            print("no apt packages")
            return
        output = self._show_info(pkgs)
        infos = self._parse_info(output)
        for i in range(len(pkgs)):
            print(f"get apt package info {i}/{len(pkgs)}: ", pkgs[i].name)
            parsed_pkg, parsed_desc, parsed_size = infos[i]
            assert pkgs[i].name == parsed_pkg
            pkgs[i].desc = parsed_desc
            pkgs[i].installed_size = parsed_size
        return pkgs


class PipPkgInfoFiller(PkgInfoFiller):
    def __init__(self, container) -> None:
        self.container = container
        self.client = docker.from_env()

    def _show_info(self, pkg_name):
        output = self.client.containers.run(
            self.container, "pip show " + pkg_name, remove=True, entrypoint=""
        ).decode("utf-8")
        return output

    def _parse_info(self, output):
        """
        Args:
            output: like
                        'Version: 63.2.0
                        Summary: Easily download, build, install, upgrade, and uninstall Python packages
                        Home-page: https://github.com/pypa/setuptools
                        Author: Python Packaging Authority
                        Author-email: distutils-sig@python.org
                        License:
                        Location: /usr/local/lib/python3.10/site-packages
                        Requires:
                        Required-by:'
        """
        output = output.strip().splitlines()
        desc = None
        location = None
        for line in output:
            if desc != None and location != None:
                break
            s = line.split(":")
            if s[0] == "Summary":
                desc = s[1].strip()
            elif s[0] == "Location":
                location = s[1].strip()

        return desc, location

    # not always works
    def _parse_size_info(self, output):
        """
        Args:
            output: like '7972\t/usr/local/lib/python3.10/site-packages/pip'
        """
        return round(float(output.split("\t")[0]), 2)

    def _get_size(self, location, pkg_name):
        output = self.client.containers.run(
            self.container,
            "du -sk " + location + "/" + pkg_name,
            remove=True,
            entrypoint="",
        ).decode("utf-8")

        return self._parse_size_info(output)

    def _fit_one_pkg(self, pkg):
        output = self._show_info(pkg.name)
        desc, location = self._parse_info(output)
        # size = self._get_size(location, pkg.name)
        pkg.desc = desc
        pkg.location = location

    def fit(self, pkgs):
        for i, p in enumerate(pkgs):
            print(f"get pip package info {i}/{len(pkgs)}: ", p.name)
            self._fit_one_pkg(p)
        return pkgs


class CondaPkgInfoFiller(PkgInfoFiller):
    def __init__(self, container) -> None:
        self.container = container
        self.client = docker.from_env()

    def _get_file_content(self, json_file_path):
        tmp_container = self.client.containers.create(
            self.container, f"cat {json_file_path}", detach=False, entrypoint=""
        )
        tmp_container.start()
        tmp_container.wait()
        out = tmp_container.logs(stdout=True, stderr=False, stream=True, follow=True)
        return b"".join([line for line in out])

    def _parse_one_package(self, pkg):
        pkg_path = pkg.location
        info_json_file = os.path.join(pkg_path, "info", "about.json")
        content = self._get_file_content(info_json_file).decode("utf-8")
        data = json.loads(content)
        pkg.desc = data.get("summary")

    def fit(self, pkgs):
        for i, p in enumerate(pkgs):
            print(f"get conda package info {i}/{len(pkgs)}: ", p.name)
            self._parse_one_package(p)
        return pkgs
