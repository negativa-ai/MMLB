import pandas as pd


class PkgInfoDumper(object):
    @staticmethod
    def dump_pkg_info(pkgs, container, file_name):
        names = []
        versions = []
        types = []
        containers = []
        descs = []
        for p in pkgs:
            names.append(p.name)
            versions.append(p.version)
            types.append(p.type)
            containers.append(container)
            descs.append(p.desc)
        data = {
            'package': names,
            'version': versions,
            'package_type': types,
            'container': containers,
            'desc': descs
        }
        df = pd.DataFrame(data=data)
        df.to_csv(file_name, index=False)

    @staticmethod
    def dump_pkg_files_info(pkgs, container, file_name, mode='w'):
        paths = []
        sizes = []
        pkg_names = []
        versions = []
        pkg_types = []
        containers = []
        for p in pkgs:
            for f in p.files:
                paths.append(f.name)
                sizes.append(f.size)
                pkg_names.append(p.name)
                versions.append(p.version)
                pkg_types.append(p.type)
                containers.append(container)
        data = {
            'path': paths,
            'size(KB)': sizes,
            'package': pkg_names,
            'version': versions,
            'package_type': pkg_types,
            'container': containers
        }
        df = pd.DataFrame(data=data)
        df.to_csv(file_name, index=False)
