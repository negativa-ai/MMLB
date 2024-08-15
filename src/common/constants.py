from enum import Enum


class Functionality(Enum):
    Debloat = 'debloat'  # debloat an image
    Diff = 'diff'  # Diff two images,
    VUL_ANALYSIS = 'vul_analysis' # vulnerability analysis
    PKG_ANALYSIS = 'pkg_analysis' # package analysis
    PKG_DEPS_ANALYSIS = 'pkg_deps_analysis' # package dependencies analysis
