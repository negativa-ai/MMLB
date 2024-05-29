from enum import Enum


class Functionality(Enum):
    Debloat = 'debloat'  # debloat an image
    Diff = 'diff'  # Diff two images,
    VUL_ANALYSIS = 'vul_analysis' # vulnerability analysis
