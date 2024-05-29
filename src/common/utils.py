import os
import random
import string
import subprocess
from typing import List

import docker
from termcolor import colored


def shell(cmd, use_popen=False):
    print("[shell]:", colored(cmd, "yellow"))
    if not use_popen:
        os.system(cmd)
        return None
    else:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc


def check_if_contains(proc, text):
    """
    Check if the output of a the process contains the flag text
    """
    while True:
        line = proc.stdout.readline().decode("utf-8")
        if text in line:
            return True
        if line == "":
            break
    return False


def generate_container_name(image: str) -> str:
    return image.replace(':', '_').replace(
        '/', '-') + '_'+''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def image_to_filename(image: str) -> str:
    return image.replace(':', '_').replace(
        '/', '-')


def contains(s: str, output: List[str]) -> bool:
    for o in output:
        if s in o:
            return True
    return False


def get_image_size(image_name: str) -> int:
    """
    Return image size in bytes
    """
    api_client: docker.APIClient = docker.APIClient(
        base_url='unix://var/run/docker.sock')
    config = api_client.inspect_image(image_name)
    return config['Size']


def is_empty_str(s: str) -> bool:
    return s == '' or s is None
