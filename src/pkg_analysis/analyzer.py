from abc import ABC, abstractmethod
import logging
import json
import os

import docker

from .package import AptPackage, CondaPackage, PipPackage





class PkgAnalyzer(ABC):
    @abstractmethod
    def list_pkgs(self) -> str:
        raise NotImplementedError()


class AptPkgAnalyzer(object):
    def __init__(self, container):
        self.container = container
        self.client = docker.from_env()

    def _detect_pkgs(self):
        output = ""
        try:
            output = self.client.containers.run(
                self.container, "apt list --installed", remove=True, entrypoint=""
            ).decode("utf-8")
        except docker.errors.APIError:
            logging.error("apt not exist")
        return output

    def _parse_one_pkg(self, line):
        """
        Args:
            line: looks like  'libbz2-1.0/now 1.0.8-5build1 arm64 [installed,local]'
        """
        s = line.split(" ")

        # no idea about what behind '/' is
        name = s[0].split("/")[0].strip()
        version = s[1].strip()
        return AptPackage(name, version)

    def _parse_pkgs(self, output):

        output = output.strip().splitlines()

        count = 0
        for index, line in enumerate(output):
            if line == "Listing... Done" or line == "Listing...":
                count = index
                break
        output = output[count + 1 :]

        pkgs = []
        for line in output:
            line = line.strip()
            if line != "":
                pkgs.append(self._parse_one_pkg(line))

        return pkgs

    def list_pkgs(self):
        output = self._detect_pkgs()
        return self._parse_pkgs(output)


class PipPkgAnalyzer(object):
    def __init__(self, container):
        self.container = container
        self.client = docker.from_env()

    def _detect_pkgs(self):
        output = ""
        try:
            output = self.client.containers.run(
                self.container, "pip list --format=freeze", remove=True, entrypoint=""
            ).decode("utf-8")
        except docker.errors.APIError:
            logging.error("pip not exist")
        return output

    def _parse_one_pkg(self, line):
        """
        Args:
            line: like 'setuptools==63.2.0'
        """

        s = line.split("==")
        if len(s) != 2:
            return None

        # cython == Cython
        return PipPackage(s[0].strip().replace("_", "-").lower(), s[1])

    def _parse_pkgs(self, output):
        output = output.strip().splitlines()

        # filter irrelevant msgs
        count = -1
        for i, s in enumerate(output):
            if "nvidia-docker run --shm-size=1g --ulimit memlock=-1" in s:
                count = i
                break
        output = output[count + 1 :]

        pkgs = []

        for line in output:
            if line != "":
                p = self._parse_one_pkg(line.strip())
                if p is not None:
                    pkgs.append(p)

        return pkgs

    def list_pkgs(self):
        output = self._detect_pkgs()
        return self._parse_pkgs(output)


class CondaPkgAnalyzer(object):
    def __init__(self, container):
        self.container = container
        self.client = docker.from_env()

    def _display_pkg_root_dirs(self):
        try:
            output = self.client.containers.run(
                self.container,
                "conda config --show pkgs_dirs",
                remove=True,
                entrypoint="",
            ).decode("utf-8")
        except docker.errors.APIError:
            logging.error("Conda might not exist")
            return ""
        return output

    def _parse_pkg_root_dirs(self, output):
        """
        Args:
            output:
                pkgs_dirs:
                    - /opt/conda/pkgs
                    - /root/.conda/pkgs
        """
        output = output.strip().splitlines()
        pkg_dirs = []
        for i in output[1:]:
            pkg_dirs.append(i.strip()[2:])
        return pkg_dirs

    def _list_pkg_dirs(self, dir):
        """
        Args:
            dir:  /opt/conda/pkgs
        """
        try:
            output = self.client.containers.run(
                self.container, f"ls {dir}", remove=True, entrypoint=""
            ).decode("utf-8")
        except docker.errors.ContainerError:
            logging.error(f"{dir} not exist.")
            return ""
        return output

    def _get_file_content(self, json_file_path):
        tmp_container = self.client.containers.create(
            self.container, f"cat {json_file_path}", detach=False, entrypoint=""
        )
        tmp_container.start()
        tmp_container.wait()
        out = tmp_container.logs(stdout=True, stderr=False, stream=True, follow=True)
        return b"".join([line for line in out])

    def _parse_pkgs(self, output, dir):
        """
        Args:
            output:
                backcall-0.2.0-pyhd3eb1b0_0
                beautifulsoup4-4.11.1-py37h06a4308_0
                brotlipy-0.7.0-py37h27cfd23_1003
                bzip2-1.0.8-h7b6447c_0
                ca-certificates-2022.4.26-h06a4308_0
                certifi-2022.6.15-py37h06a4308_0
                cffi-1.15.0-py37hd667e15_1
                chardet-4.0.0-py37h06a4308_1003
                charset-normalizer-2.0.4-pyhd3eb1b0_0
                colorama-0.4.4-pyhd3eb1b0_0
                conda-4.13.0-py37h06a4308_0
            dir: /opt/conda/pkgs
        """
        packages = []
        output = output.strip().splitlines()
        for pkg_dir in output:
            pkg_path = os.path.join(dir, pkg_dir)
            info_json_file = os.path.join(pkg_path, "info", "index.json")

            # exist bug, not able to capture all logs
            # pkg_info_json = self.client.containers.run(
            #     self.container, f'cat {info_json_file}', remove=True, entrypoint='').decode("utf-8")
            pkg_info_json = self._get_file_content(info_json_file).decode("utf-8")
            try:
                data = json.loads(pkg_info_json)
            except json.decoder.JSONDecodeError:
                logging.error(f"{info_json_file} might not exist")
                continue
            pkg = CondaPackage(data["name"], data["version"])
            pkg.location = pkg_path
            packages.append(pkg)
        return packages

    def list_pkgs(self):
        packages = []
        root_dirs_output = self._display_pkg_root_dirs()
        root_dirs = self._parse_pkg_root_dirs(root_dirs_output)
        for root_dir in root_dirs:
            pkg_dirs_output = self._list_pkg_dirs(root_dir)
            pkgs = self._parse_pkgs(pkg_dirs_output, root_dir)
            packages.extend(pkgs)
        return packages
