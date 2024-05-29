import os
import random
import string
import logging
from pathlib import Path
import shutil


class ImageFile:
    """
    A class used to represent a file.
    """

    def __init__(self, name, size):
        self.name = name
        self.size = size

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return 'ImageFile({name}, {size})'.format(name=self.name, size=self.size)

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == other.name

def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))


def export_image(image_name, output_dir='./output'):
    """Return tar file and unzipped files paths of the given image
    :param image_name: image name
    :param output_dir: the output target dir
    :return: path of image tar file, path of unzipped image files
    """

    container_name = 'diff-{rand_str}'.format(rand_str=id_generator())
    create_container_cmd = 'docker create --name {container_name} {image_name}'.format(container_name=container_name,
                                                                                       image_name=image_name)
    logging.debug('create tmp container: %s', create_container_cmd)
    os.system(create_container_cmd)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    image_tar_name = trim_image_name(image_name) + '.tar'
    image_tar_path = os.path.join(output_dir, image_tar_name)
    export_container_cmd = 'docker export {container_name} > {image_tar_path}'.format(container_name=container_name,
                                                                                      image_tar_path=image_tar_path)
    logging.debug("export image files: %s", export_container_cmd)
    os.system(export_container_cmd)

    # unzip image file
    unzip_target_dir = os.path.join(output_dir, trim_image_name(image_name))
    os.mkdir(unzip_target_dir)
    unzip_image_tar_cmd = 'tar -xvf {image_tar} -C {tar_dir}'.format(
        image_tar=image_tar_path, tar_dir=unzip_target_dir)
    logging.debug('unzip image file: %s', unzip_image_tar_cmd)
    os.system(unzip_image_tar_cmd)

    rm_container_cmd = 'docker rm {container_name}'.format(
        container_name=container_name)
    logging.debug("rm tmp container: %s", rm_container_cmd)
    os.system(rm_container_cmd)

    return image_tar_path, unzip_target_dir


def save_image(image_name, output_dir='./output'):
    """Return tar file and unzipped files paths of the given image
    :param image_name: image name
    :param output_dir: the output target dir
    :return: path of image tar file, path of unzipped image files
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    image_tar_name = trim_image_name(image_name) + '.tar'
    image_tar_path = os.path.join(output_dir, image_tar_name)
    save_container_cmd = 'docker save {image_name} > {image_tar_path}'.format(image_name=image_name,
                                                                              image_tar_path=image_tar_path)
    os.system(save_container_cmd)
    logging.debug("save image files: %s", save_container_cmd)

    # unzip image file
    unzip_target_dir = os.path.join(output_dir, trim_image_name(image_name))
    os.mkdir(unzip_target_dir)
    unzip_image_tar_cmd = 'tar -xzf {image_tar} -C {tar_dir}'.format(
        image_tar=image_tar_path, tar_dir=unzip_target_dir)
    logging.debug('unzip image file: %s', unzip_image_tar_cmd)
    os.system(unzip_image_tar_cmd)

    return image_tar_path, unzip_target_dir


def get_all_files(path):
    f"""Get all files of given path
    :param path: given path
    :return: list of {ImageFile("file_name_without_given_path", "file_size")}
    """
    du_cmd = 'find {dir} -type f -exec ls -lsd --block-size=k {{}} +'.format(
        dir=path)
    logging.debug("du command: %s", du_cmd)
    lines = os.popen(du_cmd).read().split('\n')
    image_files = []
    total_size = 0
    for line in lines:
        items = line.split()
        if len(items) == 10:
            if items[1] == 'total':
                total_size += int(items[0])
            else:
                image_files.append(
                    ImageFile(items[-1][len(path):], items[5][:-1]))
    image_files.sort(key=lambda i: i.name, reverse=True)
    return image_files, total_size


def diff_dirs(path0, path1):
    """Diff files of given paths
    :param path0:
    :param path1:
    :return:files only in path0, files only in path 1
    """
    files0, total_size0 = get_all_files(path0)
    files0_set = set(files0)

    files1, total_size1 = get_all_files(path1)
    files1_set = set(files1)

    intersection = files0_set.intersection(files1_set)

    files_only_in_path0 = files0_set - intersection

    files_only_in_path1 = files1_set - intersection

    return sorted(files_only_in_path0, key=lambda i: i.name), sorted(intersection, key=lambda i: i.name), sorted(files_only_in_path1, key=lambda i: i.name)


def trim_image_name(image_name):
    """Get image name only.

    :param image_name: image name, a string
    :return: image name only
    """
    return image_name.split('/')[-1].split(':')[0]


def write_image_files(image_files, target_path):
    """Write list of {ImageFile}s to target_path in csv format.
    :param image_files: list of {ImageFile}s
    :param target_path: output target path
    :return: 
    """
    with open(target_path, 'w+') as f:
        f.write('name,size(KB)\n')
        total_size = 0
        for i in image_files:
            f.write('{name},{size}\n'.format(name=i.name, size=i.size))
            total_size += int(i.size)


def diff_images(image0, image1, image0_output_path, common_file_output_path, image1_output_path):
    output_dir = '/tmp/output'
    os.mkdir(output_dir)

    logging.debug('start analyze: {image0} and {image1}.'.format(
        image0=image0, image1=image1))
    logging.debug('output dir is {output_dir}.'.format(output_dir=output_dir))

    tar0, dir0 = export_image(image0, output_dir)
    tar1, dir1 = export_image(image1, output_dir)
    files_only_in_path0, common_files, files_only_in_path1 = diff_dirs(
        dir0, dir1)

    write_image_files(files_only_in_path0, image0_output_path)
    logging.info('files only in {image_name} are written into file {file_name}'.format(image_name=image0,
                                                                                       file_name=image0_output_path))

    write_image_files(common_files, common_file_output_path)
    logging.info(f'common files are written into {common_file_output_path}')

    write_image_files(files_only_in_path1, image1_output_path)
    logging.info('files only in {image_name} are written into file {file_name}'.format(image_name=image1,
                                                                                       file_name=image1_output_path))
    shutil.rmtree(output_dir)
    # os.rmdir(output_dir)
