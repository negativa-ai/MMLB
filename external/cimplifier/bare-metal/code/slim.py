import os
import sys
import stat
from collections import defaultdict
import json
import tarfile
from tempfile import TemporaryDirectory
import mmap
import subprocess
import shlex
import shutil
import logging
from itertools import combinations

import arrow

import utils
import allfiles
import straceparser


# we can make this function more efficient by reducing the # of mmaps
def reduce_environ(files, envkeys):
    envkeys = {bytes(key, 'utf-8') for key in envkeys}
    not_accessed = set(envkeys)
    for file in files:
        with open(file, 'rb') as f:
            contents = f.read()
            not_accessed -= {key for key in not_accessed if key in contents}
    envkeys = envkeys - not_accessed
    return {key.decode('utf-8') for key in envkeys}


def make_img_metadata():
    img_metadata = json.loads(allfiles.configtemplate)
    allfiles.addid(img_metadata)
    img_metadata['created'] = str(arrow.utcnow())
    return img_metadata


def make_img_skeleton(name, numlayers, ismain, oldimg):
    layerid = None
    layerids = []
    for i in range(numlayers):
        metadata = make_img_metadata()
        if layerid:
            metadata['parent'] = layerid
        layerid = metadata['id']
        if ismain:
            allfiles.copy_img_metadata(oldimg, metadata)
        layerids.append(layerid)
        os.makedirs(os.path.join(name, layerid))
        with open(os.path.join(name, layerid, 'VERSION'), 'w') as f:
            f.write('1.0')  # anything should work
        with open(os.path.join(name, layerid, 'json'), 'w') as f:
            json.dump(metadata, f)
    with open(os.path.join(name, 'repositories'), 'w') as f:
        json.dump({name: {'latest': layerid}}, f)
    layers = [os.path.join(name, layerid, 'layer.tar') for layerid in layerids]
    return name, layers


def make_img_tar(name, imgdir):
    # finally, tar everything for docker load
    with tarfile.TarFile(name, mode='w') as tar:
        for filename in os.listdir(imgdir):
            tar.add(os.path.join(imgdir, filename), arcname=filename)

# we will use normpath, even though it may not be accurate
# assume path has no leading '/'. As such, due to the way this function is
# written, it behaves as if the path is first stripped of its leading '/'


def rooted_realpath(path, tree):
    original = os.path.normpath(path)
    components = original.split('/')
    path = ''
    for component in components:
        path = os.path.join(path, component)
        fullpath = os.path.join(tree, path)
        if os.path.islink(fullpath):
            newpath = os.readlink(fullpath)
            if os.path.isabs(newpath):
                path = newpath[1:]
            else:
                path = os.path.join(os.path.dirname(path), newpath)
    return path


def add_links_and_parents(tree, paths):
    paths = list(paths)  # make a local copy of paths to modify
    paths_with_parents = set()  # docker does not allow redundant paths
    for path in paths:
        original = path
        ancestor_paths = []
        while path:  # our paths are relative so final dirname will be ''
            if path == '/':
                break
            ancestor_paths.append(path)
            dirname = os.path.dirname(path)
            fullpath = os.path.join(tree, path)
            if os.path.islink(fullpath):
                # clear descendents, which should be accessed from realpath
                ancestor_paths = [path]
                newpath = os.readlink(fullpath)
                if os.path.isabs(newpath):
                    newpath = newpath[1:]
                else:
                    newpath = os.path.join(dirname, newpath)
                if os.path.lexists(os.path.join(tree, newpath)):
                    paths.append(os.path.normpath(newpath))
                    # graft the descendent path onto newpath
                    # our paths are normpath'ed so this is a bit easier
                    if path != original:
                        graft = os.path.relpath(original, path)
                        paths.append(os.path.normpath(os.path.join(newpath,
                                                                   graft)))
            path = dirname
        paths_with_parents.update(ancestor_paths)
    # make paths sorted
    return sorted(paths_with_parents)

# Python's tar implementation is too slow, so we added a new implementation
# based on the tar utility (compatible with both BSD and GNU tar)


def make_layer_tar(name, tree, paths,stubpaths, exepath):
    name = os.path.abspath(name)

    # normalize stubpaths and exepath
    # os.path.normpath is not needed because
    stubpaths = [rooted_realpath(p, tree) for p in stubpaths]
    exepath = rooted_realpath(exepath, tree)

    # add the main tree
    # we will use a list of files to add from tree. We will also explicitly add
    # parent dirs to the list. We will also recursively add all symlinks
    paths_with_parents = add_links_and_parents(tree, paths)
    # filter out the stubpaths that will be added later
    paths_w_pars_filtered = [p for p in paths_with_parents if p not in
                             stubpaths]
    # copy files from the exported original container dir.
    with utils.tmpfilename() as tfname:
        with open(tfname, 'w') as f:
            for path in paths_w_pars_filtered:
                abs_path = os.path.join(tree, path)
                if os.path.exists(abs_path) or os.path.islink(abs_path):
                    f.write('{}\n'.format(path))
        cmd = 'tar -cf {} --no-recursion -T {}'.format(name, tfname)
        subprocess.check_call(shlex.split(cmd), cwd=tree)
        

def make_volume_all_paths(name, tree):
    name = os.path.abspath(name)
    try:
        os.makedirs(name)
    except:
        return
    cmd = 'tar c . | tar x -C {}'.format(name)
    subprocess.check_call(cmd, shell=True, cwd=tree)


# TODO this does not work correctly with symlinks linking to absolute paths
# e.g., /lib64/ld-linux-x86-64.so.2
def file_isreg(path):
    ''' tell whether path is a regular file or a link ultimately leading to a
    regular file '''
    try:
        res = os.stat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(res.st_mode)


def isancestor(ancpath, despath):
    if ancpath[-1] == '/':
        ancpath = ancpath[:-1]
    return despath.startswith(ancpath) and (len(despath) == len(ancpath) or
                                            despath[len(ancpath)] == '/')


def reduce_volumes(files, volumes):
    def vol_accessed(vol):
        for file in files:
            if isancestor(vol, file):
                return True
        return False
    red_vols = list(filter(vol_accessed, volumes))
    return red_vols


def lexisting_ancestors(tree, paths):
    for p in paths:
        try:
            p = eval(f'b\"{p}\".decode()')
        except UnicodeDecodeError as e:
            print(e)
            continue
        original = p
        exists_link = False
        while p:  # p is relative
            if os.path.islink(os.path.join(tree, p)):
                exists_link = True
            if os.path.lexists(os.path.join(tree, p)):
                yield p
                if exists_link:
                    yield original
                break
            p = os.path.dirname(p)


def make_container(name, tree, ismain, oldimg, files, envkeys, cntnr_metadata, selfexepath,
                   exepaths):
    print(name)
    # we will remove the leading slash to make paths relative to tar
    paths = [file[1:] for file in files]
    # filter to keep only existing paths
    paths = set(lexisting_ancestors(tree, paths))

    # remove the leading slash to make exepaths relative; filter out the
    # selfexe. Remove leading hash for selfexepath also
    exepaths = [path[1:] for path in exepaths if path != selfexepath]
    selfexepath = selfexepath[1:]
    imgdir, layertars = make_img_skeleton(name, 1, ismain, oldimg)
    make_layer_tar(layertars[0], tree, paths, exepaths, selfexepath)
    
    make_img_tar(name+'.tar', imgdir)

    # TODO we are not yet checking env vars that may be accessed from mounted
    #   volumes
    reg_files = filter(file_isreg, [os.path.join(tree, p) for
                                    p in paths])
    reduced_envkeys = reduce_environ(reg_files, envkeys)
    # print(reduced_envkeys)

    volumes = cntnr_metadata['Mounts']
    reduced_vol_dests = reduce_volumes(files,
                                       [vol['Destination'] for vol in volumes])
    reduced_volumes = [vol for vol in volumes if vol['Destination'] in
                       reduced_vol_dests]
    print(reduced_volumes)

    return reduced_envkeys, reduced_volumes, cntnr_metadata['Config']['WorkingDir']


def remove_dynamic_paths(paths):
    dynroots = ['/dev', '/proc', '/sys']
    return [p for p in paths if not any((p.startswith(x) for x in dynroots))]


def interpreter(path):
    try:
        with open(path, 'rb') as f:
            if f.read(2) == b'#!':
                return f.readline().decode('utf-8').split(None, maxsplit=1)[0]
    except FileNotFoundError:
        # check if file is a link: absolute links will be evaluated to the host
        # root and may not be found
        if os.path.islink(path):
            # TODO: this is not the right handling; we should evaluate the link
            # ourselves or do some chroot in another process
            return None
        # files of a container are dependent on the command to run it.
        logging.exception("File not found.")
        


linkers = ['/lib/ld-linux.so.2',
           '/lib64/ld-linux-x86-64.so.2',
           '/lib/ld-musl-x86_64.so.1']  # last one is on alpine


class Context(object):
    def __init__(self, tree, execrec, ismain=False):
        self.tree = tree
        # no need for argv, children, cwd
        # keep keys only for env
        self.exe = execrec.exe  # the first exe is kind of an ID for the object
        self.exes = [execrec.exe]
        # print(execrec.envp)
        self.envkeys = {kv.split('=', maxsplit=1)[0] for kv in execrec.envp}
        self.exist_files = set(execrec.exist_files)
        self.exist_files.update(linkers)  # linker files are read implicitly
        interp = interpreter(os.path.join(self.tree, execrec.exe[1:]))
        if interp:
            self.exist_files.add(interp)
        self.written_files = set(execrec.written_files)
        self.connects = list(execrec.connects)
        self.binds = list(execrec.binds)
        self.exec_files = {execrec.exec_file} if execrec.exec_file else set()
        self.ismain = ismain

    def merge(self, execrec, addexe=False):
        if addexe:
            self.exes.append(execrec.exe)
        self.envkeys.update((kv.split('=', maxsplit=1)[0] for kv in
                             execrec.envp))
        self.exist_files.update(execrec.exist_files)
        interp = interpreter(os.path.join(self.tree, execrec.exe[1:]))
        if interp:
            self.exist_files.add(interp)
        self.written_files.update(execrec.written_files)
        self.connects.extend(execrec.connects)
        self.binds.extend(execrec.binds)
        if execrec.exec_file:
            self.exec_files.add(execrec.exec_file)

    def normpaths(self):
        self.exes = set(map(os.path.normpath, self.exes))
        self.exist_files = map(os.path.normpath, self.exist_files)
        self.written_files = map(os.path.normpath, self.written_files)
        self.exist_files = set(remove_dynamic_paths(self.exist_files))
        self.written_files = set(remove_dynamic_paths(self.written_files))
        # not normalization but very important
        self.exec_files = set(
            map(os.path.normpath, self.exec_files)) - self.exes
        print(self.exes)
        print(self.exec_files)
        # print(self.exist_files)


def tovolpath(path, tree):
    return rooted_realpath(path[1:], tree).replace('/', '_')

# a context function returns (rec, exec_vols, stubpaths) pairs


def allonecontext(pid_records, tree):
    exec_records = [rec for pidrec in pid_records.values() for rec in
                    pidrec.exec_records]
    # merge records based on rec.exe.
    print("first exec record: ", exec_records[0])
    merged_record = Context(tree, exec_records[0], ismain=True)
    # Merge resources, like read/write files of each exe/process into one context.
    for rec in exec_records[1:]:
        merged_record.merge(rec)
    merged_record.normpaths()
    
    return [merged_record]

def lisdir(path):
    ''' tell whether path is a regular file or a link ultimately leading to a
    regular file '''
    try:
        res = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(res.st_mode)

def slim(oldimg, newimgprefix, cntnr, rootpid, traces_log_file, volpath=None):
    cntnr_metadata = allfiles.cntnr_metadata(cntnr)

    # analyze strace logs
    pid_records = straceparser.process(rootpid, traces_log_file,
                                       cntnr_metadata['Config']['WorkingDir'], False)

    assert len(pid_records.values())==1
    print("exec records len: ", {len(rec.exec_records) for rec in pid_records.values()})
    # Get and refine all exec file extracted from strace log. pid_records.values()[0].exec_records is a list
    for pidrec in pid_records.values():
        for i in range(len(pidrec.exec_records)):
            if pidrec.exec_records[i].exe is None:
                del pidrec.exec_records[i]
                break
    
    for pidrec in pid_records.values():
         for rec in pidrec.exec_records:
             print('rec: ', len(rec.exist_files))

    config = {}

    with utils.tmpdirname() as tree:
        # create old container and export the container
        allfiles.make_tree(oldimg, tree)
        #allfiles.make_tree_by_container(cntnr, tree)
        print('tree done')

        # allonecontext means slimming a container
        exec_records = allonecontext(pid_records, tree)
        assert len(exec_records)==1
    
        for rec in exec_records:
            dirname, basename = os.path.split(rec.exe)
            newcntnrname = '{}_{}_{}'.format(newimgprefix,
                                             os.path.basename(dirname), basename)
            cntnrconfig = {}
            config[newcntnrname] = cntnrconfig
            envkeys, vols, wd = make_container(newcntnrname, tree, rec.ismain,
                                               oldimg, rec.exist_files, rec.envkeys, cntnr_metadata,
                                               rec.exe, [])
            
            cntnrconfig['envkeys'] = list(envkeys)
            if volpath != None:
                for vol in vols:
                    if vol['Source'].startswith('/var/lib/docker/volumes'):
                        src = os.path.join(volpath, vol['Destination'][1:])
                        vol['Source'] = src
                        subtree = os.path.join(tree, vol['Destination'][1:])
                        make_volume_all_paths(src, subtree)
            cntnrconfig['vols'] = list(vols)
            cntnrconfig['wd'] = wd
            cntnrconfig['cmd'] = '/walls/wexec /' + rooted_realpath(rec.exe[1:],
                                                                    tree)
            cntnrconfig['ismain'] = rec.ismain

    with open('{}.json'.format(newimgprefix), 'w') as f:
        json.dump({'config': config, 'original_container': cntnr_metadata}, f)

if __name__ == '__main__':
    slim(*sys.argv[1:])
    