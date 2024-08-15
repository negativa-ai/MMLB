from abc import ABC, abstractmethod
import os

import docker

from .package import PkgFile


class PkgFileFiller(ABC):
    @abstractmethod
    def fit(self, pkgs):
        """
        Args:
            pkgs: list of Package

        Returns:
            None

        Fill the files field of each package in pkgs.
        """
        raise NotImplementedError()


class AptPkgFileFiller(PkgFileFiller):
    def __init__(self, container) -> None:
        self.container = container
        self.client = docker.from_env()

    def _list_files(self, pkg_name):

        raw_output = self.client.containers.run(
            self.container, "dpkg -L " + pkg_name, remove=True, entrypoint=""
        ).decode("utf-8")
        pkg_files = raw_output.splitlines()
        # filter irrelevant msgs
        # count = 0
        # for i, s in enumerate(pkg_files):
        #     if s.startswith('/'):
        #         count = i
        #         break
        # pkg_files = pkg_files[count:]

        filter_pkg_files = set({})
        quote_pkg_files = []
        # package dash in ubuntu would produce something like 'package diverts others to'
        for line in pkg_files:
            if (
                "package diverts others to:" not in line
                and "locally diverted to: " not in line
                and "diverted by bash to:" not in line
            ):

                filter_pkg_files.add(line)
                quote_pkg_files.append("'" + line + "'")

        ls_files = " ".join(quote_pkg_files)
        try:
            output = self.client.containers.run(
                self.container,
                "ls -lsd --block-size=k " + ls_files,
                remove=True,
                entrypoint="",
            ).decode("utf-8")
        except docker.errors.ContainerError as e:
            # remove these non exist files
            non_exist_files = set({})
            err_msg = e.stderr.decode("utf-8").splitlines()
            for line in err_msg:
                path = line.replace("ls: cannot access ", "", 1).replace(
                    ": No such file or directory", "", 1
                )[1:-1]
                non_exist_files.add(path)
            exist_file = filter_pkg_files - non_exist_files
            quote_pkg_files = []
            for f in exist_file:
                quote_pkg_files.append("'" + f + "'")

            output = self.client.containers.run(
                self.container,
                "ls -lsd --block-size=k " + " ".join(quote_pkg_files),
                remove=True,
                entrypoint="",
            ).decode("utf-8")
        return output

    def _parse_line(self, line):
        """
        Args:
            line: like `40K -rwxr-xr-x   1 root root 38K Jan  6  2021 '/usr/sbin/adduser'`
        """
        count = -1
        for i, e in enumerate(line):
            if e == "'":
                count = i
                break
        if count != -1:
            file_name = line[count:][1:-1]
            line = line[:count]
        else:
            file_name = line.split()[9].strip()

        s = line.split()
        file = None
        # we only care about files and links.
        if s[1].strip().startswith("-") or s[1].strip().startswith("l"):
            size = float(s[5].strip()[:-1])
            file = PkgFile(file_name, size)
        return file

    def _parse_files(self, output):
        files = []
        output = output.splitlines()

        # filter irrelevant msgs
        # count = -1
        # for i, s in enumerate(output):
        #     if 'nvidia-docker run --shm-size=1g --ulimit memlock=-1' in s:
        #         count = i
        #         break
        # output = output[count+1:]

        for line in output:
            line = line.strip()
            if "No such file or directory" not in line and line != "" and line != "\n":
                file = self._parse_line(line)
                if file is not None:
                    files.append(file)
        return files

    def fit(self, pkgs):
        for i, p in enumerate(pkgs):
            print(f"get apt package files {i}/{len(pkgs)}: ", p.name)
            files_str = self._list_files(p.name)
            files = self._parse_files(files_str)
            p.files = files
            for f in p.files:
                p.occupied_size += f.size


class PipPkgFileFiller(PkgFileFiller):
    def __init__(self, container) -> None:
        self.container = container
        self.client = docker.from_env()

    def _list_files(self, pkg_name, pkg_location):
        raw_output = (
            self.client.containers.run(
                self.container, "pip show -f " + pkg_name, remove=True, entrypoint=""
            )
            .decode("utf-8")
            .splitlines()
        )

        # filter irrelevant msg
        count = -1
        for i, s in enumerate(raw_output):
            if s.startswith("Files:"):
                count = i
                break

        pkg_files = raw_output[count + 1 :]

        # if can't locate installed files by `pip show -f pkg`, we find them by search directory
        # the output like `Cannot locate installed-files.txt` or 'Cannot locate RECORD or installed-files.txt'
        if (
            len(pkg_files) == 1
            and "Cannot locate" in pkg_files[0]
            and "installed-files.txt" in pkg_files[0]
        ):
            # print('cant locate installed files by pip')
            try:
                # ugly code, special care for PyYAML package
                if pkg_name.lower() == "pyyaml":
                    pkg_name = "yaml"

                # not work for pip pkg like  keyrings.alt-3.0.egg-info
                raw_output = (
                    self.client.containers.run(
                        self.container,
                        "find " + pkg_location + "/" + pkg_name,
                        remove=True,
                        entrypoint="",
                    )
                    .decode("utf-8")
                    .splitlines()
                )

                # filter irrelevant msgs
                # count = -1
                # for i, s in enumerate(raw_output):
                #     if 'nvidia-docker run --shm-size=1g --ulimit memlock=-1' in s:
                #         count = i
                #         break
                # filter_output = raw_output[count+1:]

                pkg_files = []
                for line in raw_output:
                    if line.strip() != "":
                        pkg_files.append(line)
            except docker.errors.ContainerError as e:
                err_msg = e.stderr.decode("utf-8")
                print("cannot locate files of pip pkg: ", pkg_name)
                print(e)
                return ""

        else:
            for i in range(len(pkg_files)):
                pkg_files[i] = pkg_location + "/" + pkg_files[i].strip()

        quote_pkg_files = []
        for line in pkg_files:
            quote_pkg_files.append("'" + line + "'")
        try:
            output = self.client.containers.run(
                self.container,
                "ls -lsd --block-size=k " + " ".join(quote_pkg_files),
                remove=True,
                entrypoint="",
            ).decode("utf-8")
        except docker.errors.ContainerError as e:
            # remove these non exist files
            non_exist_files = set({})
            err_msg = e.stderr.decode("utf-8").splitlines()
            for line in err_msg:
                non_exist_files.add(line.split()[3][1:-2])
            exist_file = set(pkg_files) - non_exist_files
            quote_pkg_files = []
            for f in exist_file:
                quote_pkg_files.append("'" + f + "'")

            output = self.client.containers.run(
                self.container,
                "ls -lsd --block-size=k " + " ".join(quote_pkg_files),
                remove=True,
                entrypoint="",
            ).decode("utf-8")

        return output

    def _parse_line(self, line):
        """
        Args:
            line: like `40K -rwxr-xr-x   1 root root 38K Jan  6  2021 '/usr/sbin/adduser'`
        """
        count = -1
        for i, e in enumerate(line):
            if e == "'":
                count = i
                break
        if count != -1:
            file_name = line[count:][1:-1]
            line = line[:count]
        else:
            file_name = line.split()[-1].strip()

        s = line.split()
        file = None
        # we only care about files.
        if s[1].strip().startswith("-"):
            size = float(s[5].strip()[:-1])
            file = PkgFile(file_name, size)
        return file

    def _parse_files(self, output):
        files = []
        output = output.splitlines()

        # filter irrelevant msgs
        # count = -1
        # for i, s in enumerate(output):
        #     if 'nvidia-docker run --shm-size=1g --ulimit memlock=-1' in s:
        #         count = i
        #         break
        # output = output[count+1:]

        for line in output:
            line = line.strip()
            if "No such file or directory" not in line and line != "" and line != "\n":
                file = self._parse_line(line)
                if file is not None:
                    files.append(file)
        return files

    def fit(self, pkgs):
        for i, p in enumerate(pkgs):
            print(f"get pip package files {i}/{len(pkgs)}: ", p.name)
            files_str = self._list_files(p.name, p.location)
            files = self._parse_files(files_str)
            p.files = files
            for f in p.files:
                p.occupied_size += f.size


class CondaPkgFileFiller(PkgFileFiller):
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

    def _list_files(self, pkg):
        files_path = os.path.join(pkg.location, "info", "files")
        output = self._get_file_content(files_path).decode("utf-8")

        pkg_files = output.strip().splitlines()
        for i in range(len(pkg_files)):
            pkg_files[i] = os.path.join(pkg.location, pkg_files[i])

        quote_pkg_files = []
        for line in pkg_files:
            if "package diverts others to:" not in line:
                quote_pkg_files.append("'" + line + "'")
        ls_files = " ".join(quote_pkg_files)

        output = self.client.containers.run(
            self.container,
            "ls -lsd --block-size=k " + ls_files,
            remove=True,
            entrypoint="",
        ).decode("utf-8")
        return output

    def _parse_line(self, line):
        """
        Args:
            line: like `40K -rwxr-xr-x   1 root root 38K Jan  6  2021 '/usr/sbin/adduser'`
        """
        count = -1
        for i, e in enumerate(line):
            if e == "'":
                count = i
                break
        if count != -1:
            file_name = line[count:][1:-1]
            line = line[:count]
        else:
            file_name = line.split()[-1].strip()

        s = line.split()
        file = None
        # we only care about files.
        if s[1].strip().startswith("-"):
            size = float(s[5].strip()[:-1])
            file = PkgFile(file_name, size)
        return file

    def _parse_files(self, output):
        files = []
        output = output.splitlines()

        for line in output:
            line = line.strip()
            if "No such file or directory" not in line and line != "" and line != "\n":
                file = self._parse_line(line)
                if file is not None:
                    files.append(file)
        return files

    def fit(self, pkgs):
        for i, p in enumerate(pkgs):
            print(f"get conda package files {i}/{len(pkgs)}: ", p.name)
            files_str = self._list_files(p)
            files = self._parse_files(files_str)
            p.files = files
            for f in p.files:
                p.occupied_size += f.size
