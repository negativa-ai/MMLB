import os
import shutil
import docker
import logging
import queue
from typing import List, Dict
import time
from docker.types import Mount as DockerMount

from common.utils import shell, generate_container_name, contains


class Mount(object):
    def __init__(self, source: str, target: str, create_src: bool, delete_src: bool) -> None:
        """
            Params:
                                source: source dir
                                target: target dir
                create_src: create the source dir if it doesn't exist
                delete_src: delete the source dir after finishing tasks
        """
        self.source: str = source
        self.target: str = target
        self.create_src: bool = create_src
        self.delete_src: bool = delete_src
        self.mount: DockerMount = DockerMount(
            source=source, target=target, type='bind')


class ContainerTestCase(object):
    def __init__(self, name: str, cmd: str, cmd_output: str = '', container_output: str = '') -> None:
        self.name = name
        self.cmd = cmd
        self.cmd_output = cmd_output
        self.container_output = container_output

    def run(self) -> List[str]:
        if self.cmd == '':
            return []
        proc = shell(self.cmd, use_popen=True)
        output: List[str] = []
        while True:
            line = proc.stdout.readline().decode('utf-8')
            if line == '':
                break
            output.append(line)
        return output


class Container(object):
    def __init__(self, image: str, mounts: List[Mount] = None, test_cases: List[ContainerTestCase] = None,
                 cmd: str = '', long_running: bool = False, flag_text: str = '', ports: Dict[str, str] = {}, environment: List[str] = []):
        self.image: str = image
        self.mounts: List[Mount] = mounts
        self.test_cases: List[ContainerTestCase] = test_cases
        self.cmd: str = cmd
        self.long_running: bool = long_running
        self.flag_text: str = flag_text
        self.ports: Dict[str, str] = ports
        self.environment: List[str] = environment

        self.name: str = ''
        self.id: str = ''

        self.api_client: docker.APIClient = docker.APIClient(
            base_url='unix://var/run/docker.sock')
        self.client: docker.DockerClient = docker.from_env()

        self.log_queue: queue.Queue = queue.Queue()

    def setup(self) -> None:
        for m in self.mounts:
            if m.create_src:
                try:
                    os.mkdir(m.source)
                except FileExistsError as e:
                    logging.warn(f'fail to create dit: {m.source}, {e}')

    def run_container(self, **kwargs) -> None:
        """
        Return when a container runs successfully, or else blocking
        """
        envs = kwargs.get('environment', [])
        for e in envs:
            self.environment.append(e)
        kwargs['environment'] = self.environment

        self.name = generate_container_name(self.image)
        docker_mounts: List[DockerMount] = []
        for m in self.mounts:
            docker_mounts.append(m.mount)

        if not self.long_running:
            self.client.containers.run(
                image=self.image, command=self.cmd, auto_remove=False,
                mounts=docker_mounts, detach=False, entrypoint='',
                ports=self.ports, name=self.name, **kwargs)
            self.id = self.api_client.containers(
                filters={'name': self.name}, all=True)[0]['Id']
        else:
            self.client.containers.run(
                image=self.image, command=self.cmd, auto_remove=False,
                mounts=docker_mounts, detach=True, entrypoint='',
                ports=self.ports, name=self.name, **kwargs)
            self.id = self.api_client.containers(
                filters={'name': self.name})[0]['Id']
            if self.flag_text == '':
                time.sleep(3)
                return
            logs = self.api_client.logs(
                container=self.name, stream=True)
            for content_bytes in logs:
                log = content_bytes.decode('utf-8')
                if self.flag_text in log:
                    # make sure the service has started
                    time.sleep(1)
                    return

    def get_output(self) -> List[str]:
        print(self.name)
        return self.api_client.logs(
            container=self.name, stream=False).decode('utf-8').split('\n')

    def run_test_cases(self):
        for t in self.test_cases:
            if t.cmd != '':
                test_cmd_output = t.run()
                container_output = self.get_output()
                if not contains(t.cmd_output, test_cmd_output) or \
                        not contains(t.container_output, container_output):
                    return False
            else:
                container_output = self.get_output()
                if not contains(t.container_output, container_output):
                    return False
        return True

    def cleanup(self) -> None:
        if self.long_running:
            self.api_client.stop(self.name)
        self.api_client.remove_container(self.name)
        for m in self.mounts:
            if m.delete_src:
                shutil.rmtree(m.source)


def clone_container(container: Container) -> Container:
    if not isinstance(container, Container):
        return None

    return Container(image=container.image, mounts=container.mounts, test_cases=container.test_cases,
                     cmd=container.cmd, long_running=container.long_running, flag_text=container.flag_text, ports=container.ports)
