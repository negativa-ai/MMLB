import argparse
import json
import logging
import os
from typing import List, Dict

import pandas as pd
import yaml

from common.utils import get_image_size, image_to_filename, is_empty_str
from common.constants import Functionality
from container import Container, Mount, ContainerTestCase
from debloater import Cimplifier, Debloater
from image_diff import diff_images
from vul_analysis.vul_analysis import ContainerCreator
from pkg_analysis.dependency_graph import PipDependencyGraph, AptDependencyGraph
from pkg_analysis.analyzer import AptPkgAnalyzer, CondaPkgAnalyzer, PipPkgAnalyzer
from pkg_analysis.package_info import (
    AptPkgInfoFiller,
    CondaPkgInfoFiller,
    PipPkgInfoFiller,
)
from pkg_analysis.package_file import (
    AptPkgFileFiller,
    CondaPkgFileFiller,
    PipPkgFileFiller,
)
from pkg_analysis.dump_pkg_info import PkgInfoDumper
from pkg_analysis.image import Image


def yaml_to_containers(yaml_path: str) -> List[Container]:
    containers: List[Container] = []
    with open(yaml_path, "r") as f:
        imgs = yaml.safe_load(f)
        for k, v in imgs.items():
            mounts: List[Mount] = []
            for m in v.get("mounts", []):
                mount = Mount(
                    source=m["source"],
                    target=m["target"],
                    create_src=m["create_src"],
                    delete_src=m["delete_src"],
                )
                mounts.append(mount)

            ports: Dict[str, str] = {}
            for p in v.get("ports", []):
                host_port, container_port = p.split(":")
                ports[f"{container_port}/tcp"] = host_port

            envs: List[str] = []
            for e in v.get("environment", []):
                envs.append(e)

            test_cases: List[ContainerTestCase] = []
            for t in v.get("test_cases", []):
                test_case = ContainerTestCase(
                    name=t["name"],
                    cmd=t.get("cmd", ""),
                    cmd_output=t.get("cmd_output", ""),
                    container_output=t.get("container_output", ""),
                )
                test_cases.append(test_case)
            container: Container = Container(
                image=k,
                mounts=mounts,
                test_cases=test_cases,
                cmd=v["cmd"],
                long_running=v["long_running"],
                flag_text=v.get("flag_text", ""),
                ports=ports,
                environment=envs,
            )
            containers.append(container)
    return containers


def debloat_containers(yaml_path: str, output_path: str):
    containers: List[Container] = yaml_to_containers(yaml_path)

    debloater: Debloater = Cimplifier(
        debloat_cmd=os.getenv("CIMPLIFIER_SLIM_PATH"),
        import_cmd=os.getenv("CIMPLIFIER_IMPORT_PATH"),
    )
    results = {
        "original_image_name": [],
        "debloated_image_name": [],
        "original_image_size": [],
        "debloated_image_size": [],
        "cmd": [],
    }
    for c in containers:
        try:
            original_size = get_image_size(c.image)
            debloated_image_name = debloater.debloat(c)
            debloated_size = get_image_size(debloated_image_name)
            results["original_image_name"].append(c.image)
            results["debloated_image_name"].append(debloated_image_name)
            results["original_image_size"].append(original_size)
            results["debloated_image_size"].append(debloated_size)
            results["cmd"].append(c.cmd)
        except Exception as e:
            pd.DataFrame(results).to_csv(output_path, index=False)
            raise e

    pd.DataFrame(results).to_csv(output_path, index=False)


def diff_all_images(csv_path: str, final_res_path: str, diff_res_path: str):
    df = pd.read_csv(csv_path)
    image_meta = {}
    for _, row in df.iterrows():
        original_image = row["original_image_name"]
        debloated_image = row["debloated_image_name"]
        original_image_file_path = os.path.join(
            diff_res_path, image_to_filename(original_image) + ".csv"
        )
        common_file_path = os.path.join(
            diff_res_path, image_to_filename(original_image) + "_common.csv"
        )
        debloated_image_file_path = os.path.join(
            diff_res_path, image_to_filename(debloated_image) + ".csv"
        )
        diff_images(
            original_image,
            debloated_image,
            original_image_file_path,
            common_file_path,
            debloated_image_file_path,
        )
        image_meta[original_image] = {}
        image_meta[original_image]["image_name"] = original_image
        image_meta[original_image]["debloated_img_name"] = debloated_image
        image_meta[original_image]["original_files_path"] = original_image_file_path
        image_meta[original_image]["common_file_path"] = common_file_path
        image_meta[original_image]["debloated_files_path"] = debloated_image_file_path
        image_meta[original_image]["cmd"] = row["cmd"]
    with open(final_res_path, "w+") as f:
        json.dump(image_meta, f)


def vul_analysis(
    img_name: str,
    debloated_img_name: str,
    cmd: str,
    result_path: str,
    pkg_cve_path: str,
):
    path = "/tmp/vuls"
    vul_res = []
    all_pkg_stats = {"img_name": [], "pkg_name": [], "pkg_type": [], "severity": []}
    working_dir = os.path.join(path)
    cc = ContainerCreator(working_dir)
    original_report, cve_by_pkg = cc.analyze_original_container(img_name, cmd)
    all_pkg_stats["img_name"].extend(
        [f"{img_name}-{n}" for n in cve_by_pkg["img_name"]]
    )
    all_pkg_stats["pkg_name"].extend(cve_by_pkg["pkg_name"])
    all_pkg_stats["pkg_type"].extend(cve_by_pkg["pkg_type"])
    all_pkg_stats["severity"].extend(cve_by_pkg["severity"])
    original_report["img"] = img_name
    original_report["type"] = "original"
    vul_res.append(original_report)
    debloated_report = cc.analyze_debloated_container(debloated_img_name, cmd)
    debloated_report["img"] = debloated_img_name
    debloated_report["type"] = "debloated"
    vul_res.append(debloated_report)
    pd.DataFrame(vul_res).to_csv(result_path, index=False)
    pd.DataFrame(all_pkg_stats).to_csv(pkg_cve_path, index=False)


def pkg_info_analysis(image_name: str):
    print(f"Analyzing packages in image: {image_name}")

    def analyze_pkgs(pkg_type: str):
        ana = None
        info_filler = None
        file_filler = None
        if pkg_type == "apt":
            ana = AptPkgAnalyzer(image_name)
            info_filler = AptPkgInfoFiller(image_name)
            file_filler = AptPkgFileFiller(image_name)
        elif pkg_type == "pip":
            ana = PipPkgAnalyzer(image_name)
            info_filler = PipPkgInfoFiller(image_name)
            file_filler = PipPkgFileFiller(image_name)
        elif pkg_type == "conda":
            ana = CondaPkgAnalyzer(image_name)
            info_filler = CondaPkgInfoFiller(image_name)
            file_filler = CondaPkgFileFiller(image_name)
        else:
            raise ValueError(f"Unknown package type: {pkg_type}")

        pkgs = ana.list_pkgs()
        info_filler.fit(pkgs)
        file_filler.fit(pkgs)
        return pkgs

    all_pkgs = []
    apt_pkgs = analyze_pkgs("apt")
    pip_pkgs = analyze_pkgs("pip")
    conda_pkgs = analyze_pkgs("conda")

    print("Dumping package analysis results...")
    dumper = PkgInfoDumper()

    all_pkgs.extend(apt_pkgs)
    all_pkgs.extend(pip_pkgs)
    all_pkgs.extend(conda_pkgs)

    pkg_info_file = image_to_filename(image_name) + "_" + "packages.csv"
    print("dump info to: ", pkg_info_file)
    dumper.dump_pkg_info(all_pkgs, image_name, pkg_info_file)

    pkg_file_info_file = image_to_filename(image_name) + "_" + "packages_files.csv"
    print("dump files info to: ", pkg_file_info_file)
    dumper.dump_pkg_files_info(all_pkgs, image_name, pkg_file_info_file)


def pkg_deps_analysis(
    image_name: str,
    debloated_image_name: str,
    removed_files_path: str,
    package_path: str,
    packge_files_path: str,
    deps_path: str,
    grype_json_path: str,
):
    pkg_df = pd.read_csv(package_path)
    pkg_files_df = pd.read_csv(packge_files_path)
    removed_files_df = pd.read_csv(removed_files_path)

    with open(grype_json_path, "r") as file:
        grype_json_str = file.read()

    with open(deps_path) as f:
        deps_content = f.readlines()

    image = Image(image_name)
    image.analyze(debloated_image_name, pkg_files_df, removed_files_df, pkg_df)
    image.pkg_bloat_degrees.to_csv("tmp.csv")

    def generate_deps_graph(pkg_type):
        """
        pkg_type: str, 'pip' or 'apt'
        """
        direct_accessed_pkgs = []
        indices = (
            image.pkg_bloat_degrees.query(f'package_type=="{pkg_type}"')
            .query("bloat_degree<1")
            .index.tolist()
        )
        for p in indices:
            direct_accessed_pkgs.append((p[0], p[2]))

        dep_graph = None
        if pkg_type == "pip":
            dep_graph = PipDependencyGraph(
                deps_content, direct_accessed_pkgs=direct_accessed_pkgs
            )
        else:
            pkg_names = []
            for i in indices:
                pkg_names.append(i[0])
            dep_graph = AptDependencyGraph(image_name, direct_accessed_pkgs=pkg_names)

        dep_graph.build(image.pkg_bloat_degrees, grype_json=grype_json_str)
        dep_graph.generate_fig(f"{image_to_filename(image_name)}_{pkg_type}", "./")

    generate_deps_graph("pip")
    generate_deps_graph("apt")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s-%(levelname)s- %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="MMLB",
        description="Measure machine learning containers bloat",
        epilog="help text to be added",
    )
    parser.add_argument("--func", type=str, help="Specify functionality you want")
    # arguments for debloating function
    parser.add_argument("--container_spec", type=str, help="path to the yaml spec file")
    parser.add_argument(
        "--output",
        type=str,
        help="output path of debloating results, shoudl be a csv file",
    )

    # arguments for image diff function
    parser.add_argument("--i1", type=str, help="the first image name")
    parser.add_argument("--i2", type=str, help="the second image name")
    parser.add_argument(
        "--i1_path", type=str, help="this file contains the files only exist in i1"
    )
    parser.add_argument(
        "--common_file_path",
        type=str,
        help="this file contains the files existing in i1 and i2",
    )
    parser.add_argument(
        "--i2_path", type=str, help="this file contains the files only exist in i2"
    )

    parser.add_argument(
        "--csv_path",
        type=str,
        help="the .csv file generated by the debloating functionality, used as the input for image diff",
    )
    parser.add_argument(
        "--diff_res_path",
        type=str,
        help="the path to store the result file path of each image",
    )
    parser.add_argument(
        "--final_res_path",
        type=str,
        help="the final output of image diff. It is a json file, recording the diff result file path of each image.",
    )

    # arguments for vul analysis
    parser.add_argument("--img_name", type=str, help="Original image name")
    parser.add_argument("--debloated_img_name", type=str, help="Debloated image name")
    parser.add_argument("--cmd", type=str, help="Command line to start the container")
    parser.add_argument(
        "--cve_number_path", type=str, help="numbers of each cves severity file"
    )
    parser.add_argument(
        "--pkg_cve_number_path", type=str, help="numbers of cves in each package file"
    )

    # arguments for deps analysis
    parser.add_argument(
        "--removed_files_path", type=str, help="the path of removed files csv file"
    )
    parser.add_argument("--package_path", type=str, help="the path of package csv file")
    parser.add_argument(
        "--package_files_path", type=str, help="the path of package files csv file"
    )
    parser.add_argument("--deps_path", type=str, help="the path of dependency file")
    parser.add_argument(
        "--grype_json_path", type=str, help="the path of grype json file"
    )

    args = parser.parse_args()

    func = args.func

    if func == Functionality.Debloat.value:
        debloat_containers(args.container_spec, args.output)
    elif func == Functionality.Diff.value:
        if not is_empty_str(args.i1):
            diff_images(
                args.i1, args.i2, args.i1_path, args.common_file_path, args.i2_path
            )
        else:
            diff_all_images(args.csv_path, args.final_res_path, args.diff_res_path)
    elif func == Functionality.VUL_ANALYSIS.value:
        vul_analysis(
            args.img_name,
            args.debloated_img_name,
            args.cmd,
            args.cve_number_path,
            args.pkg_cve_number_path,
        )
    elif func == Functionality.PKG_ANALYSIS.value:
        containers: List[Container] = yaml_to_containers(args.container_spec)
        for c in containers:
            pkg_info_analysis(c.image)
    elif func == Functionality.PKG_DEPS_ANALYSIS.value:
        pkg_deps_analysis(
            args.img_name,
            args.debloated_img_name,
            args.removed_files_path,
            args.package_path,
            args.package_files_path,
            args.deps_path,
            args.grype_json_path,
        )
