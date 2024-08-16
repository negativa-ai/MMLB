from abc import ABC, abstractmethod
import logging
import json
import queue

import docker
import graphviz

from .package import PipPackage, AptPackage


class DepsGraph(ABC):
    @abstractmethod
    def build(self, pkg_bloat_degrees_df=None, grype_json='{"matches":[]}'):
        raise NotImplementedError()

    @abstractmethod
    def traverse(self, node_func=None, edge_func=None, start_node=None):
        raise NotImplementedError()


# pip dependency graph
class PipDependencyGraph(DepsGraph):
    def __init__(self, deps_file_content, direct_accessed_pkgs=None) -> None:
        """
        deps_file_content: array of content of deps.txt, split by lines,
        users could specify the start points by giving direct_accessed_pkgs
        """
        self.deps_file_content = deps_file_content
        self.root_node = PipGraphNode(
            "app",
            "0",
        )
        self.root_node.type = "root"
        self.vul_table = {}
        self.table = {}
        self.direct_accessed_packages = direct_accessed_pkgs

    def _parse_grpye_json(self, grype_json):
        data = json.loads(grype_json)["matches"]
        for v in data:
            name = v["artifact"]["name"]
            version = v["artifact"]["version"]
            type = v["artifact"]["type"]
            if type == "python":
                node = PipGraphNode(name=name, version=version)
                if node not in self.vul_table:
                    self.vul_table[node] = 1
                else:
                    self.vul_table[node] += 1

    def _parse_file_content(self, all_content):
        for i, line in enumerate(all_content):
            if line.strip() == "project_level_deps:":
                all_deps_arr = all_content[1:i]
                project_deps_arr = all_content[i + 1 :]

        all_deps = json.loads("".join(all_deps_arr))

        project_deps = []
        for line in project_deps_arr:
            c = line.split("==")
            if len(c) == 2:
                # absl_py --> absl-py
                project_deps.append(
                    (c[0].strip().replace("_", "-").lower(), c[1].strip())
                )

        return all_deps, project_deps

    def _pase_all_deps(self, all_deps):
        """
        Returns a table storing the whole deps graph.
        """
        table = {}
        for dep in all_deps:
            name = dep["package"]["package_name"].strip().replace("_", "-").lower()
            version = dep["package"]["installed_version"]
            key = name + "_" + version
            if key in table:
                pkg = table[key]
            else:
                pkg = PipGraphNode(name=name, version=version)
                table[key] = pkg

            sub_deps = dep["dependencies"]
            for sub_dep in sub_deps:
                name = sub_dep["package_name"].strip().replace("_", "-").lower()
                version = sub_dep["installed_version"]
                key = name + "_" + version
                if key in table:
                    sub_pkg = table[key]
                else:
                    sub_pkg = PipGraphNode(name=name, version=version)
                    table[key] = sub_pkg

                pkg.deps.add(sub_pkg)
        return table

    def _parse_deps(self, all_deps_table, project_deps):
        for dep in project_deps:
            key = dep[0].strip().replace("_", "-").lower() + "_" + dep[1].strip()
            # pipreqs is used to detect project-level dependenies,
            # pipdeptree is used to detect all pip packages
            # sometimes pipreqs just report some unexist packages.
            # so we ignore those unexist packages
            if key not in all_deps_table:
                logging.error(f"package {key} not found")
                # raise Exception(f'package {key} not found')
                continue
            node = all_deps_table[key]

            self.root_node.deps.add(node)

        return self.root_node

    def build(self, pkg_bloat_degrees_df=None, grype_json='{"matches":[]}'):
        """
        pkg_bloat_degrees_df:  obtained from Image.analyze()
        """
        if len(self.deps_file_content) <= 1:
            return self.root_node

        self._parse_grpye_json(grype_json)

        all_deps, project_deps = self._parse_file_content(self.deps_file_content)
        all_deps_table = self._pase_all_deps(all_deps)
        if self.direct_accessed_packages is not None:
            project_deps = self.direct_accessed_packages
        self._parse_deps(all_deps_table, project_deps)
        if pkg_bloat_degrees_df is None:
            return self.root_node

        def fill_bloat_degree_and_table(node):
            index = (node.name, node.type, node.version)
            if index in pkg_bloat_degrees_df.index:
                bloat_degree = pkg_bloat_degrees_df.loc[index]["bloat_degree"]
                node.bloat_degree = bloat_degree
                size = pkg_bloat_degrees_df.loc[index]["size(KB)_total"]
                node.size = size
                self.table[node.name + "_" + node.version] = node
                if node in self.vul_table:
                    node.num_vuls = self.vul_table[node]
            else:
                logging.error(f"{index} not found in bloat_degree_df")

        self.traverse(node_func=fill_bloat_degree_and_table)

        return self.root_node

    def apply_to_pd(self, node, node_func):
        self.traverse(node_func=node_func, start_node=node)

    def apply_to_pr(self, node, node_func):
        to_be_visited = queue.Queue()
        visited = set()

        to_be_visited.put(node)
        while not to_be_visited.empty():
            n = to_be_visited.get()
            if n not in visited:
                node_func(n)
                visited.add(n)
                for _, graph_node in self.table.items():
                    if node in graph_node.deps and graph_node not in visited:
                        to_be_visited.put(graph_node)

    def traverse(self, node_func=None, edge_func=None, start_node=None):
        """
        node_func takes a node as a parameter.
        edge_func taks 2 nodes as parameters.

        """
        to_be_visited = queue.Queue()
        visited = set()
        if start_node is None:
            start_node = self.root_node
        to_be_visited.put(start_node)
        while not to_be_visited.empty():
            n = to_be_visited.get()
            if n not in visited:
                if node_func is not None:
                    node_func(n)
                visited.add(n)
                for sub_n in n.deps:
                    if sub_n.depth == 0:
                        sub_n.depth = n.depth + 1
                    if edge_func is not None:
                        edge_func(n, sub_n)
                    to_be_visited.put(sub_n)

    def generate_fig(self, name, path="./"):
        dot = graphviz.Digraph(name, comment="deps graph")

        def generate_node(node):
            label = node.name + "\n" + str(node.depth)
            if node.bloat_degree is not None:
                label += "\n" + str(round(node.bloat_degree, 2))
            label += "\n" + str(node.num_vuls)
            dot.node(str(node), label)

        def generate_edge(nodeA, nodeB):
            dot.edge(str(nodeA), str(nodeB))

        self.traverse(node_func=generate_node)
        self.traverse(edge_func=generate_edge)
        dot.render(directory=path)
        logging.debug(dot.source)

    def generate_sbom(self, is_debloated=False):
        sbom = {"packages": []}

        def generate_entry(node):
            if node.name == "app" and node.depth == 0:
                return
            if is_debloated:
                if node.bloat_degree == 1 or node.bloat_degree is None:
                    return
                entry = {
                    "name": node.name,
                    "version": node.version,
                    "depth": node.depth,
                    "type": "PIP",
                    "size": int(node.size * node.bloat_degree),
                    "dependencies": [],
                }
                for n in node.deps:
                    if node.bloat_degree == 1 or node.bloat_degree is None:
                        continue
                    entry["dependencies"].append(
                        {
                            "name": n.name,
                            "version": n.version,
                            "depth": n.depth,
                            "type": "PIP",
                            "size": int(n.size * node.bloat_degree),
                        }
                    )
            else:
                entry = {
                    "name": node.name,
                    "version": node.version,
                    "depth": node.depth,
                    "type": "PIP",
                    "size": node.size,
                    "dependencies": [],
                }
                for n in node.deps:
                    entry["dependencies"].append(
                        {
                            "name": n.name,
                            "version": n.version,
                            "depth": n.depth,
                            "type": "PIP",
                            "size": n.size,
                        }
                    )
            sbom["packages"].append(entry)

        self.traverse(node_func=generate_entry)
        print("num of pip:", len(sbom["packages"]))
        return sbom


# pip dependency node
class PipGraphNode(PipPackage):
    def __init__(
        self,
        name,
        version,
        bloat_degree=None,
        desc=None,
        size=-1,
    ):
        super().__init__(name, version, desc, size)
        self.deps = set()
        self.bloat_degree = bloat_degree
        self.depth = 0
        self.num_vuls = 0

    def __hash__(self) -> int:
        return hash(self.name + ":" + self.version)

    def __eq__(self, __o: object) -> bool:
        return (
            self.__class__ == __o.__class__
            and self.name == __o.name
            and self.version == __o.version
        )

    def __str__(self) -> str:
        return self.name + "_" + self.version

    def __repr__(self) -> str:
        return self.name + "_" + self.version


# apt list + apt-cache rdepends libname to create the dependency graph
# check the top level dependency manually


class AptDependencyGraph(DepsGraph):
    def __init__(self, container_name, direct_accessed_pkgs) -> None:
        """
        pkg_names: a list of direct deps pkg names.
        """
        self.container_name = container_name
        self.pkg_names = direct_accessed_pkgs
        self.root_node = AptGraphNode("app")
        self.table = {}
        self.root_node.type = "root"
        self.vul_table = {}

    def _parse_grpye_json(self, grype_json):
        data = json.loads(grype_json)["matches"]
        for v in data:
            name = v["artifact"]["name"]
            version = v["artifact"]["version"]
            type = v["artifact"]["type"]
            if type == "deb":
                node = AptGraphNode(name=name)
                if node not in self.vul_table:
                    self.vul_table[node] = 1
                else:
                    self.vul_table[node] += 1

    def _show_pkg_depends(self, pkg_names):
        """
        cmd: apt depends --installed libnvinfer7 librtmp1
        output:
        libnvinfer7
            Depends: libcudnn8
        librtmp1
            Depends: libc6 (>= 2.14)
            Depends: libgmp10
            Depends: libgnutls30 (>= 3.4.2)
            Depends: libhogweed4
            Depends: libnettle6
            Depends: zlib1g (>= 1:1.1.4)
        """
        self.client = docker.from_env()
        cmd = "apt depends --installed "
        for p in pkg_names:
            cmd += p + " "
        raw_output = self.client.containers.run(
            self.container_name, cmd, remove=True, entrypoint=""
        ).decode("utf-8")
        arr_output = raw_output.splitlines()
        return arr_output

    def _create_sub_graph(self, arr_output):
        cur_pkg_ind = 0
        cur_id = 0
        while cur_id < len(arr_output):
            cur_pkg_name = arr_output[cur_pkg_ind]
            if cur_pkg_name in self.table:
                node = self.table[cur_pkg_name]
            else:
                node = AptGraphNode(cur_pkg_name)
                self.table[cur_pkg_name] = node

            cur_id = cur_pkg_ind + 1
            if cur_id >= len(arr_output):
                break
            cur_line = arr_output[cur_id]
            while cur_line.startswith(" "):
                if "Depends" in cur_line:
                    arrs = cur_line.split()
                    dep_pkg_name = arrs[1].strip()
                    if dep_pkg_name in self.table:
                        sub_node = self.table[dep_pkg_name]
                    else:
                        sub_node = AptGraphNode(dep_pkg_name)
                        self.table[dep_pkg_name] = sub_node
                    node.deps.add(sub_node)
                cur_id += 1
                if cur_id >= len(arr_output):
                    break
                cur_line = arr_output[cur_id]
            cur_pkg_ind = cur_id

        for name in self.pkg_names:
            if name not in self.table:
                if name == "tensorflow-model-server":
                    continue
                raise Exception(f"package {name} is not analyzed")
            node = self.table[name]
            self.root_node.deps.add(node)
        return self.root_node

    def _create_whole_graph(self, arr_output):
        self._create_sub_graph(arr_output)
        while True:
            logging.info("generate graph another round...")
            prev_size = len(self.table)
            all_nodes = list(self.table.values())
            new_pkg_names = []
            for v in all_nodes:
                for d in v.deps:
                    if d not in self.table:
                        new_pkg_names.append(d.name)
            if len(new_pkg_names) > 0:
                arr_output = self._show_pkg_depends(new_pkg_names)
                self._create_sub_graph(arr_output)
            if prev_size == len(self.table):
                break
        return self.root_node

    def traverse(self, node_func=None, edge_func=None, start_node=None):
        """
        node_func takes a node as a parameter.
        edge_func taks 2 nodes as parameters.

        """
        to_be_visited = queue.Queue()
        visited = set()
        if start_node is None:
            start_node = self.root_node
        to_be_visited.put(start_node)
        while not to_be_visited.empty():
            n = to_be_visited.get()
            if n not in visited:
                if node_func is not None:
                    node_func(n)
                visited.add(n)
                for sub_n in n.deps:
                    if sub_n.depth == 0:
                        sub_n.depth = n.depth + 1
                    if edge_func is not None:
                        edge_func(n, sub_n)
                    to_be_visited.put(sub_n)

    def apply_to_pd(self, node, node_func):
        self.traverse(node_func=node_func, start_node=node)

    def apply_to_pr(self, node, node_func):
        to_be_visited = queue.Queue()
        visited = set()

        to_be_visited.put(node)
        while not to_be_visited.empty():
            n = to_be_visited.get()
            if n not in visited:
                node_func(n)
                visited.add(n)
                for _, graph_node in self.table.items():
                    if node in graph_node.deps and graph_node not in visited:
                        to_be_visited.put(graph_node)

    def generate_fig(self, name, path="./"):
        dot = graphviz.Digraph(name, comment=name)

        def generate_node(node):
            label = node.name + "\n" + str(node.depth)
            if node.bloat_degree is not None:
                label += "\n" + str(round(node.bloat_degree, 2))
            label += "\n" + str(node.num_vuls)

            dot.node(str(node), label)

        def generate_edge(nodeA, nodeB):
            dot.edge(str(nodeA), str(nodeB))

        self.traverse(node_func=generate_node)
        self.traverse(edge_func=generate_edge)
        dot.render(directory=path)
        logging.debug(dot.source)

    def generate_sbom(self, is_debloated=False):
        sbom = {"packages": []}

        def generate_entry(node):
            if node.name == "app" and node.depth == 0:
                return
            if is_debloated:
                if node.bloat_degree == 1:
                    return
                entry = {
                    "name": node.name,
                    "version": node.version,
                    "depth": node.depth,
                    "type": "APT",
                    "size": int(node.size * node.bloat_degree),
                    "dependencies": [],
                }
                for n in node.deps:
                    if node.bloat_degree == 1 or node.bloat_degree is None:
                        continue
                    entry["dependencies"].append(
                        {
                            "name": n.name,
                            "version": n.version,
                            "depth": n.depth,
                            "type": "APT",
                            "size": int(n.size * node.bloat_degree),
                        }
                    )
            else:
                entry = {
                    "name": node.name,
                    "version": node.version,
                    "depth": node.depth,
                    "type": "APT",
                    "size": node.size,
                    "dependencies": [],
                }
                for n in node.deps:
                    entry["dependencies"].append(
                        {
                            "name": n.name,
                            "version": n.version,
                            "depth": n.depth,
                            "type": "APT",
                            "size": n.size,
                        }
                    )
            sbom["packages"].append(entry)

        self.traverse(node_func=generate_entry)
        print("num of apt:", len(sbom["packages"]))
        return sbom

    def build(self, pkg_bloat_degrees_df=None, grype_json='{"matches":[]}'):
        self._parse_grpye_json(grype_json)

        arr_output = self._show_pkg_depends(self.pkg_names)
        self._create_whole_graph(arr_output)
        if pkg_bloat_degrees_df is None:
            return self.root_node

        def fill_bloat_degree(node):
            res = pkg_bloat_degrees_df.query(
                f'package=="{node.name}" and package_type=="apt"'
            )
            if len(res) != 1 and node != self.root_node:
                logging.error(
                    f"Expect matched only one package: {node.name}, get: {len(res)}"
                )
                # raise Exception(
                #     f'Expect matched only one package: {node.name}')
            if node != self.root_node:
                node.bloat_degree = res["bloat_degree"][0]
                node.size = res["size(KB)_total"][0]
                if node in self.vul_table:
                    node.num_vuls = self.vul_table[node]

        self.traverse(node_func=fill_bloat_degree)
        return self.root_node


class AptGraphNode(AptPackage):
    def __init__(
        self,
        name,
        version=None,
        bloat_degree=None,
        desc=None,
        size=-1,
    ):
        super().__init__(name, version, desc, size)
        self.deps = set()
        self.depth = 0
        self.bloat_degree = bloat_degree
        self.num_vuls = 0

    # here we don't treat version as an identifier, as 'apt depends' command
    # doesn't accept a version as an argument.
    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, __o: object) -> bool:
        return self.__class__ == __o.__class__ and self.name == __o.name

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return self.name
