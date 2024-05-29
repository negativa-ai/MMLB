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


def yaml_to_containers(yaml_path: str) -> List[Container]:
    containers: List[Container] = []
    with open(yaml_path, 'r') as f:
        imgs = yaml.safe_load(f)
        for k, v in imgs.items():
            mounts: List[Mount] = []
            for m in v.get('mounts', []):
                mount = Mount(source=m['source'], target=m['target'],
                              create_src=m['create_src'], delete_src=m['delete_src'])
                mounts.append(mount)

            ports: Dict[str, str] = {}
            for p in v.get('ports', []):
                host_port, container_port = p.split(':')
                ports[f'{container_port}/tcp'] = host_port

            envs: List[str] = []
            for e in v.get('environment', []):
                envs.append(e)

            test_cases: List[ContainerTestCase] = []
            for t in v.get('test_cases', []):
                test_case = ContainerTestCase(
                    name=t['name'], cmd=t.get('cmd', ''), cmd_output=t.get('cmd_output', ''),
                    container_output=t.get('container_output', ''))
                test_cases.append(test_case)
            container: Container = Container(image=k, mounts=mounts, test_cases=test_cases,
                                             cmd=v['cmd'], long_running=v['long_running'],
                                             flag_text=v.get('flag_text', ''), ports=ports, environment=envs)
            containers.append(container)
    return containers


def debloat_containers(yaml_path: str, output_path: str):
    containers: List[Container] = yaml_to_containers(yaml_path)
    
    debloater: Debloater = Cimplifier(debloat_cmd=os.getenv('CIMPLIFIER_SLIM_PATH'),
                                      import_cmd=os.getenv('CIMPLIFIER_IMPORT_PATH'))
    results = {
        'original_image_name': [],
        'debloated_image_name': [],
        'original_image_size': [],
        'debloated_image_size': [],
        'cmd': []
    }
    for c in containers:
        try:
            original_size = get_image_size(c.image)
            debloated_image_name = debloater.debloat(c)
            debloated_size = get_image_size(debloated_image_name)
            results['original_image_name'].append(c.image)
            results['debloated_image_name'].append(debloated_image_name)
            results['original_image_size'].append(original_size)
            results['debloated_image_size'].append(debloated_size)
            results['cmd'].append(c.cmd)
        except Exception as e:
            pd.DataFrame(results).to_csv(output_path, index=False)
            raise e

    pd.DataFrame(results).to_csv(output_path, index=False)


def diff_all_images(csv_path: str, final_res_path: str, diff_res_path: str):
    df = pd.read_csv(csv_path)
    image_meta = {}
    for _, row in df.iterrows():
        original_image = row['original_image_name']
        debloated_image = row['debloated_image_name']
        original_image_file_path = os.path.join(
            diff_res_path, image_to_filename(original_image)+'.csv')
        common_file_path = os.path.join(
            diff_res_path, image_to_filename(original_image)+'_common.csv')
        debloated_image_file_path = os.path.join(
            diff_res_path, image_to_filename(debloated_image)+'.csv')
        diff_images(original_image, debloated_image,
                    original_image_file_path, common_file_path, debloated_image_file_path)
        image_meta[original_image] = {}
        image_meta[original_image]['image_name'] = original_image
        image_meta[original_image]['debloated_img_name'] = debloated_image
        image_meta[original_image]['original_files_path'] = original_image_file_path
        image_meta[original_image]['common_file_path'] = common_file_path
        image_meta[original_image]['debloated_files_path'] = debloated_image_file_path
        image_meta[original_image]['cmd'] = row['cmd']
    with open(final_res_path, 'w+') as f:
        json.dump(image_meta, f)


def vul_analysis(img_meta_path: str, result_path: str, pkg_cve_path: str):
    with open(img_meta_path) as f:
        data = json.load(f)
    path = '/tmp/vuls'
    vul_res = []
    all_pkg_stats = {
        'img_name': [],
        'pkg_name': [],
        'pkg_type': [],
        'severity': []
    }
    for k, v in data.items():
        original_img = v['image_name']
        debloated_img = v['debloated_img_name']
        cmd = v['cmd']
        working_dir = os.path.join(path, k)
        cc = ContainerCreator(working_dir)
        original_report, cve_by_pkg = cc.analyze_original_container(
            original_img, cmd)
        all_pkg_stats['img_name'].extend(
            [f'{k}-{n}' for n in cve_by_pkg['img_name']])
        all_pkg_stats['pkg_name'].extend(cve_by_pkg['pkg_name'])
        all_pkg_stats['pkg_type'].extend(cve_by_pkg['pkg_type'])
        all_pkg_stats['severity'].extend(cve_by_pkg['severity'])
        original_report['img'] = original_img
        original_report['key']=k
        vul_res.append(original_report)
        debloated_report = cc.analyze_debloated_container(debloated_img, cmd)
        debloated_report['img'] = debloated_img
        debloated_report['key']=k
        vul_res.append(debloated_report)
    pd.DataFrame(vul_res).to_csv(result_path, index=False)
    pd.DataFrame(all_pkg_stats).to_csv(pkg_cve_path, index=False)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s-%(levelname)s- %(message)s')
    parser = argparse.ArgumentParser(
        prog='MMLB', description='Measure machine learning containers bloat', epilog='help text to be added')
    parser.add_argument('--func', type=str,
                        help='Specify functionality you want')
    # arguments for debloating function
    parser.add_argument('--container_spec', type=str,
                        help='path to the yaml spec file')
    parser.add_argument('--output', type=str,
                        help='output path of debloating results, shoudl be a csv file')

    # arguments for image diff function
    parser.add_argument('--i1', type=str, help='the first image name')
    parser.add_argument('--i2', type=str, help='the second image name')
    parser.add_argument('--i1_path', type=str,
                        help='this file contains the files only exist in i1')
    parser.add_argument('--common_file_path', type=str,
                        help='this file contains the files existing in i1 and i2')
    parser.add_argument('--i2_path', type=str,
                        help='this file contains the files only exist in i2')

    parser.add_argument('--csv_path', type=str,
                        help='the .csv file generated by the debloating functionality, used as the input for image diff')
    parser.add_argument('--diff_res_path', type=str,
                        help='the path to store the result file path of each image')
    parser.add_argument('--final_res_path', type=str,
                        help='the final output of image diff. It is a json file, recording the diff result file path of each image.')

    # arguments for vul analysis
    parser.add_argument('--img_meta_path', type=str,
                        help='the path of image meta json file')
    parser.add_argument('--cve_number_path', type=str,
                        help='numbers of each cves severity file')
    parser.add_argument('--pkg_cve_number_path', type=str,
                        help='numbers of cves in each package file')

    args = parser.parse_args()

    func = args.func

    if func == Functionality.Debloat.value:
        debloat_containers(args.container_spec, args.output)
    elif func == Functionality.Diff.value:
        if not is_empty_str(args.i1):
            diff_images(args.i1, args.i2, args.i1_path,
                        args.common_file_path, args.i2_path)
        else:
            diff_all_images(args.csv_path, args.final_res_path,
                            args.diff_res_path)
    elif func == Functionality.VUL_ANALYSIS.value:
        vul_analysis(args.img_meta_path, args.cve_number_path,
                     args.pkg_cve_number_path)
