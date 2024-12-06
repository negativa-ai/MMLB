import tempfile
from contextlib import contextmanager
import os
import re

@contextmanager
def tmpfilename():
    ''' makes a temporary file and gives you a name '''
    f = tempfile.NamedTemporaryFile(delete=False)
    name = f.name
    f.close()
    yield name
    os.unlink(name)

tmpdirname = tempfile.TemporaryDirectory


# not an exact regex for localhost ipv6 but works for us as we expect to match
# with valid IPs only
localhostipv6re = re.compile(r'(0*:)*0*1')
# also matches ::ffff:127.0.0.1
localhostre = re.compile(r'.*127\.[0-9]*\.[0-9]*\.[0-9]*')
def islocalhost(ip):
    if localhostre.match(ip):
        return True
    if localhostipv6re.match(ip):
        return True
    return False

