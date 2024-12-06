import os
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
import tarfile
import random
import json
import logging

import docker
# from docker import Client
import arrow

configtemplate = '''
{
    "architecture": "amd64",
    "config": {
        "AttachStderr": false,
        "AttachStdin": false,
        "AttachStdout": false,
        "Cmd": null,
        "Domainname": "",
        "Entrypoint": null,
        "Env": null,
        "ExposedPorts": null,
        "Hostname": "",
        "Image": "",
        "Labels": null,
        "MacAddress": "",
        "NetworkDisabled": false,
        "OnBuild": null,
        "OpenStdin": false,
        "PublishService": "",
        "StdinOnce": false,
        "Tty": false,
        "User": "",
        "VolumeDriver": "",
        "Volumes": null,
        "WorkingDir": ""
    },
    "container": "",
    "container_config": {
        "AttachStderr": false,
        "AttachStdin": false,
        "AttachStdout": false,
        "Cmd": [
            "/bin/sh",
            "-c",
            "#(nop) ADD files in /"
        ],
        "Domainname": "",
        "Entrypoint": null,
        "Env": null,
        "ExposedPorts": null,
        "Hostname": "",
        "Image": "",
        "Labels": null,
        "MacAddress": "",
        "NetworkDisabled": false,
        "OnBuild": null,
        "OpenStdin": false,
        "PublishService": "",
        "StdinOnce": false,
        "Tty": false,
        "User": "",
        "VolumeDriver": "",
        "Volumes": null,
        "WorkingDir": ""
    },
    "created": "",
    "docker_version": "1.9.0",
    "id": "",
    "os": "linux"
}
'''

docker_url = 'unix://var/run/docker.sock'
logging.getLogger().setLevel(logging.INFO)

def save(img_name, img_tar):
    ''' docker save ... '''
    client = docker.APIClient(base_url=docker_url)
    image = client.get_image(img_name)
    img_tar.write(image.data)
    img_tar.close()

def ordered_layers(img_name):
    ''' return layer ids in order, topmost layer first '''
    client = docker.APIClient(base_url=docker_url)
    history = client.history(img_name)
    return [data['Id'] for data in history]

def whiteout(name):
    ''' return the file name being whited out or None '''
    dir, basename = os.path.split(name)
    if basename.startswith('.wh.'):
        return os.path.join(dir, basename[4:]) # len('.wh.') == 4
    return None # being explicit

def extractlayer(layertar, tree):
    ''' extract layer in the dir tree while taking care of whiteouts '''
    with tarfile.TarFile(layertar) as tar:
        whiteouts = []
        for member in tar.getnames():
            whfile = whiteout(member)
            if whfile is not None:
                # save whiteouts for later
                whiteouts.append(member)
                # remove whited out files from previous layers
                path = os.path.join(tree, whfile)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        # now extract everything and then remove .wh. file
        tar.extractall(path=tree)
        for w in whiteouts:
            os.remove(os.path.join(tree, w))


# make_tree_old is a dropin replacement of make_tree. This was the earlier
# representation but was slower, probably due to python's slow tar
# implementation
def make_tree_old(img_name, tree):
    # docker save ...
    with NamedTemporaryFile(suffix='.tar', prefix='cpp', delete=False) as \
            layers_tar:
        save(img_name, layers_tar)
        layers_tarname = layers_tar.name

    # tar xf saved-image.tar
    # Using the python tar implementation; xattr support not needed
    with TemporaryDirectory(prefix='cpp') as layers_dir:
        with tarfile.TarFile(name=layers_tarname) as tar:
            tar.extractall(path=layers_dir)

        for layer in reversed(ordered_layers(img_name)):
            extractlayer(os.path.join(layers_dir, layer, 'layer.tar'),
                    tree)

def make_tree(img_name, tree):
    client = docker.APIClient(base_url=docker_url)
    cntnr = client.create_container(image=img_name)
    cmd = 'docker export {} | tar x -C {}'.format(cntnr['Id'], tree)
    subprocess.check_call(cmd, shell=True)
    client.remove_container(container=cntnr, v=True)

def make_tree_by_container(container_id, tree):
    cmd = 'docker export {} | tar x -C {}'.format(container_id, tree)
    subprocess.check_call(cmd, shell=True)


def addid(metadata):
    metadata['id'] = hex(random.getrandbits(256))[2:].zfill(64)

def make_metadata(origimg, addid=False):
    '''
        Refer to https://github.com/docker/docker/blob/master/image/spec/v1.md
    '''
    # docker inspect ... (image)
    client = docker.APIClient(base_url=docker_url)
    inspect = client.inspect_image(origimg)
    # copy in relevant parts to our metadata
    # note that the output of inspect_image has camel-cased keys but our json
    #   doesn't, that is wierd. The code below could break should the case
    #   change in future Docker/API 
    metadata = json.loads(configtemplate)
    metadata['architecture'] = inspect['Architecture']
    metadata['config'] = inspect['Config']
    # reset 'image' key, it probably does not matter though
    metadata['config']['Image'] = ''
    # leave container config to default, not sure why should be needed
    # add current time
    metadata['created'] = str(arrow.utcnow())
    # no worry for docker version; add the author if it is exists
    if 'Author' in inspect:
        metadata['author'] = inspect['Author']
    # add image id
    if addid:
        addid(metadata)
    return metadata

def cntnr_metadata(cntnr):
    client = docker.APIClient(base_url=docker_url)
    return client.inspect_container(cntnr)

# copy env, cmd from old image to main image
def copy_img_metadata(img, newmetadata):
    client = docker.APIClient(base_url=docker_url)
    metadata = client.inspect_image(img)
    newmetadata['config']['Env'] = metadata['Config']['Env']
    newmetadata['config']['Cmd'] = metadata['Config']['Cmd']
    if 'ExposedPorts' in metadata['Config']:
        newmetadata['config']['ExposedPorts'] = metadata['Config']['ExposedPorts']
    newmetadata['config']['Entrypoint'] = metadata['Config']['Entrypoint']
    newmetadata['config']['WorkingDir'] = metadata['Config']['WorkingDir']
    

def squash_all(img_name, new_name):
    # metadata and image id
    logging.info('gathering metadata')
    metadata = make_metadata(img_name, addid=True)
    newid = metadata['id']

    # get the image ready
    logging.info('making dir structure dumping metadata')
    os.makedirs(os.path.join(new_name, newid))
    with open(os.path.join(new_name, 'repositories'), 'w') as f:
        json.dump({new_name: {'latest': newid}}, f)
    with open(os.path.join(new_name, newid, 'VERSION'), 'w') as f:
        f.write('1.0') # anything should work
    with open(os.path.join(new_name, newid, 'json'), 'w') as f:
        json.dump(metadata, f)

    # add layer.tar
    logging.info('dumping layer.tar')
    with TemporaryDirectory(prefix='cpp') as tree:
        make_tree(img_name, tree)
        with tarfile.TarFile(os.path.join(new_name, newid, 'layer.tar'),
                mode='w') as tar:
            for name in os.listdir(tree):
                tar.add(os.path.join(tree, name), arcname=name)

    # finally, tar everything for docker load
    logging.info('preparing image for docker load')
    with tarfile.TarFile(new_name + '.tar', mode='w') as tar:
        tar.add(os.path.join(new_name, newid), arcname=newid)
        tar.add(os.path.join(new_name, 'repositories'), arcname='repositories')
    
