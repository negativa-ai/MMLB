import os
import shutil
from typing import Tuple
from common.utils import shell, image_to_filename
from .template import Debloater
from container import Container, clone_container


class Cimplifier(Debloater):

    def __init__(self, debloat_cmd: str, import_cmd: str) -> None:
        super().__init__()
        self.deboat_cmd: str = debloat_cmd
        self.import_cmd: str = import_cmd
        self.log_dir: str = '/tmp/container-trace'

    def _collect_sys_logs(self, container_id: str, image_name: str) -> Tuple[str, str]:
        short_cnt_id: str = container_id[:12]

        # get pid
        container_log_dir = os.path.join(self.log_dir, short_cnt_id)
        pid_filepath = os.path.join(container_log_dir, 'init.pid')
        with open(pid_filepath) as f:
            pid = f.readline().strip()

        merged_logs_file: str = os.path.join(
            '/tmp', f'strace_{image_name}_{short_cnt_id}.log')

        shell(f'cat {container_log_dir}/{short_cnt_id}.* > {merged_logs_file}')

        return pid, merged_logs_file

    def debloat(self, container: Container) -> str:
        container.setup()
        container.run_container(environment=['TRACE=true'])
        assert container.run_test_cases()
        pid, log_path = self._collect_sys_logs(
            container_id=container.id, image_name=container.image)
        image_prefix = 'cimplifier_debloated_' + \
            image_to_filename(container.image)
        tmp_work_dir = os.path.join('/tmp', container.name)

        if not os.path.exists(tmp_work_dir):
            os.mkdir(tmp_work_dir)

        debloat_cmd = f'cd {tmp_work_dir} && python3 {self.deboat_cmd} {container.image} {image_prefix} {container.name} {pid} {log_path}'
        shell(debloat_cmd)
        import_cmd = f'cd {tmp_work_dir} && python3 {self.import_cmd} {image_prefix}'
        proc = shell(import_cmd, use_popen=True)

        # get debloated image name
        debloated_image_name = proc.stdout.readline().decode("utf-8").strip()

        container.cleanup()
        shutil.rmtree(tmp_work_dir)

        # verify debloated container
        debloated_container = clone_container(container)
        debloated_container.image = debloated_image_name
        debloated_container.setup()
        debloated_container.run_container()
        assert debloated_container.run_test_cases()
        debloated_container.cleanup()
        print(f'debloat {container.image} success!')

        return debloated_image_name
