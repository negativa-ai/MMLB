import docker
import subprocess
import os
from pathlib import Path
import json

CRITICAL = 'Critical'
HIGH = "High"
MEDIUM = 'Medium'
LOW = 'Low'
NEGLIBILE = 'Negligible'
UNKNOWN = "Unknown"


class ContainerCreator():
    def __init__(self, working_dir) -> None:
        self.working_dir = working_dir
        if not os.path.exists(working_dir):
            os.mkdir(working_dir)

        self.vul_analysis_scripe_path = Path(__file__).with_name("search_vuln.py")
        self.api_client = docker.APIClient(
            base_url='unix://var/run/docker.sock')
        self.client = docker.from_env()

    def _run_cmd(self, cmd):
        print(f'run cmd: {cmd}')
        proc = subprocess.Popen(
            cmd, shell=True)
        proc.wait()
        proc.kill()

    def analyze_original_container(self, img_name, cmd):
        print(f'analyze container: {img_name}')
        self.grype_report = os.path.join(self.working_dir, 'grype.json')
        cwd=Path.cwd()
        final_report = os.path.join(self.working_dir, 'original.txt')
        print(final_report)
        if os.path.exists(final_report):
            print('use existing cve report')
            return self.count_cves(final_report), self.count_cves_by_pkg(img_name, self.grype_report,final_report)
        print(f'{final_report} not exist, re-analyze')
        new_dir = img_name.replace('/', '_')
        new_dir = new_dir.replace(':', '_')

        img_fs = os.path.join(self.working_dir, new_dir)
        if not os.path.exists(img_fs):
            os.mkdir(img_fs)
            os.chdir(img_fs)

            container = self.client.containers.create(
                img_name, command=cmd, entrypoint='')
            print(container.id)
            self._run_cmd(f'docker export {container.id} | tar -x')
            os.chdir(cwd)

        self._run_cmd(
            f'grype {img_fs}  -o json > {self.grype_report}')
        
        self._run_cmd(
            f'python {self.vul_analysis_scripe_path} grype {self.grype_report} {img_fs} > {final_report}')
        print(f'analyze container: {img_name} done.')

        return self.count_cves(final_report), self.count_cves_by_pkg(img_name, self.grype_report,final_report)

    def analyze_debloated_container(self, img_name, cmd):
        print(f'analyze debloated container: {img_name}')
        cwd=Path.cwd()
        final_report = os.path.join(self.working_dir, 'debloated.txt')
        if os.path.exists(final_report):
            print('use existing cve report')
            return self.count_cves(final_report)
        new_dir = img_name.replace('/', '_')
        new_dir = new_dir.replace(':', '_')

        img_fs = os.path.join(self.working_dir, new_dir)
        if not os.path.exists(img_fs):
            os.mkdir(img_fs)
            os.chdir(img_fs)

            container = self.client.containers.create(
                img_name, command=cmd, entrypoint='')
            print(container.id)
            self._run_cmd(f'docker export {container.id} | tar -x')
            os.chdir(cwd)

        self._run_cmd(
            f'python {self.vul_analysis_scripe_path} grype {self.grype_report} {img_fs} > {final_report}')
        print(f'analyze debloated container: {img_name} done.')
        return self.count_cves(final_report)

    def count_cves(self, report_path):
        with open(report_path) as file:
            stats = {
                CRITICAL: 0,
                HIGH: 0,
                MEDIUM: 0,
                LOW: 0,
                NEGLIBILE: 0,
                UNKNOWN: 0,
            }

            for i, line in enumerate(file):
                if i == 0:
                    total_num = int(line.strip())
                    continue
                if line.startswith('/'):
                    continue
                eles = line.split(' ')
                if len(eles) != 4:
                    continue
                severity = line.split(' ')[1]
                stats[severity] += 1
            expected_num = 0
            for k, v in stats.items():
                expected_num += v
            print(f'expected_num: {expected_num}, total_num: {total_num}')
            assert expected_num == total_num
            print('total: ', total_num)
            for k in stats.keys():
                print(k, stats[k])
            return stats

    def count_cves_by_pkg(self, img_name, grype_report_path,final_report_path):
        filtered_pkg_names=[]
        with open(final_report_path) as file:
            for i, line in enumerate(file):
                if i == 0:
                    total_num = int(line.strip())
                    continue
                if line.startswith('/'):
                    continue
                eles = line.strip().split(' ')
                if len(eles) != 4:
                    continue

                filtered_pkg_names.append(eles[0])

        with open(grype_report_path) as file:
            check_stats = {
                CRITICAL: [],
                HIGH: [],
                MEDIUM: [],
                LOW: [],
                NEGLIBILE: [],
                UNKNOWN: [],
            }
            pkg_stats = {
                'img_name': [],
                'pkg_name': [],
                'pkg_type': [],
                'pkg_version': [],
                'severity': [],
                'cve_id':[]
            }
            data=json.load(file)
            for m in data['matches']:
                if m['artifact']['name'] not in filtered_pkg_names:
                    continue
                pkg_stats['img_name'].append(img_name)
                pkg_stats['pkg_name'].append(m['artifact']['name'])
                pkg_stats['pkg_type'].append(m['artifact']['type'])
                pkg_stats['pkg_version'].append(m['artifact']['version'])
                severity = m['vulnerability']['severity']
                id=m['vulnerability']['id']
                pkg_stats['severity'].append(severity)
                pkg_stats['cve_id'].append(id)
                if severity in check_stats: 
                    check_stats[severity].append(id)
        with open(final_report_path) as file:
            for i, line in enumerate(file):
                if i == 0:
                    total_num = int(line.strip())
                    break
            expected_num = 0
            for k, v in check_stats.items():
                num=len(v)
                expected_num += num
            print(f'expected_num: {expected_num}, total_num: {total_num}')
            assert expected_num == total_num, f'expected_num: {expected_num}, total_num: {total_num}'

        return pkg_stats
