#!/bin/env python3

''' Parsing strace output for container partitioning.

    This parser is compatible with the following sample strace 4.10 command
    $ strace -o <trace_prefix> -y -yy -f -ff -v -s 1024 executable

    Note that some syscall handlers below handle error numbers. This is however
    not complete. Making this complete will need significant effort and a
    possibly systematic analysis/understanding of the semantics of the various
    system calls. Sometimes, some error conditions may not be possible in our
    scenario. For example, busy errors may not happen on some local file
    systems.

'''

import os
import json
from glob import glob
import re

CREAT_FLAGS = ['O_CREAT', 'O_WRONLY', 'O_TRUNC']
# regex for possible file descriptors
# for now used only for ret value, TODO used for args as well
fdre = re.compile(r'((?:0[xX][0-9a-fA-F]+)|(?:-?[0-9]+))(?:<(.*)>)?')
nop = lambda *args: None

# lambdas have expression bodies not statements, so no straight-forward way to
#   a lambda that raises exception
def unhandled(*args):
    #raise UnhandledSyscallError()
    return

def limitednop(noptimes):
    ''' for handling allowing nop on some syscalls in the beginning of
        container start
    '''
    def lnop(*args):
        nonlocal noptimes
        if not noptimes:
            raise UnhandledSyscallError()
        noptimes -= 1
    return lnop

def parse_call(line):
    '''
    "Each line in the trace contains the system call name, followed by  its
    arguments  in  parentheses  and  its return value."
    "Errors (typically a return value of -1) have the errno symbol and error
    string appended."
    Note that we assume no pids prepended to lines (e.g., use -ff option)
    '''
    syscall, rest = line.split('(', maxsplit=1)
    argstr, retstr = rest.rsplit('=', maxsplit=1)
    argstr = argstr.rstrip()[:-1] # remove trailing ')'
    # exit and exit_group do not return
    if syscall == '_exit' or syscall == 'exit_group':
        return syscall, argstr, None, None, None
    retstr = retstr.lstrip()
    m = fdre.match(retstr)
    if not m:
        print(line, end='')
        assert retstr.startswith('? ')
        end = 2
        ret, retfdpath = '-1', None
    else:
        ret, retfdpath = m.groups()
        end = m.end()
    errlist = retstr[end:].split(maxsplit=1)
    err = errlist[0] if errlist else None
    return syscall, argstr, int(ret, 0), retfdpath, err


def parse_signal(line):
    '''
    "Signals are printed as signal symbol and decoded siginfo
     structure."
    '''
    line = line.strip("---").strip()
    signal, rest = line.split(' ', maxsplit=1)
    signal_detail = {}
    if rest.startswith('{'):
        rest = rest.strip("{}")
        for item in rest.split(', '):
            k, v = item.split('=')
            signal_detail[k] = v
    if "si_pid" in signal_detail.keys():
        return signal_detail["si_signo"], signal_detail["si_code"], signal_detail["si_pid"]
    return signal, None, None


def string_arg(argstr):
    if argstr.startswith('NULL'):
        return None, True, argstr[4:]
    assert argstr[0] == '"'
    closequote = 0
    while True:
        closequote = argstr.find('"', closequote+1)
        # count the number of backslashes before closequote
        bkslsh_count = 0
        while argstr[closequote-1-bkslsh_count] == '\\':
            bkslsh_count += 1
        if bkslsh_count % 2 == 0:
            break
    arg = argstr[1:closequote]
    rest = argstr[closequote+1:]
    iscomplete = not rest.startswith('...')
    if not iscomplete:
        rest = rest[3:]
    return arg, iscomplete, rest

def next_arg(argstr):
    ''' position to the beginning of next_arg '''
    if argstr[0] == ',':
        argstr = argstr[1:]
    return argstr.lstrip()

def flags_arg(argstr):
    ''' flags are printed without space and comma, so it is easy '''
    split = argstr.split(',', maxsplit=1)
    rest = '' if len(split) == 1 else split[1]
    flags = split[0].split('|')
    return flags, rest

def int_arg(argstr):
    split = argstr.split(',', maxsplit=1)
    return int(split[0]), '' if len(split) == 1 else split[1]

def fd_arg(argstr):
    ''' this is for fd args that could also include AT_FDCWD '''
    # the parsing assume no comma in the pathname
    split = argstr.split(',', maxsplit=1)
    if split[0] == 'AT_FDCWD':
        arg = 'AT_FDCWD'
    else:
        argsplit = split[0].split('<', maxsplit=1)
        fd = int(argsplit[0])
        # :-1 below removes trailing '>'
        path = None if len(argsplit) == 1 else argsplit[1][:-1]
        arg = fd, path
    return arg, '' if len(split) == 1 else split[1]

def sockaddr_arg(argstr):
    # some examples of socket addresses
    # {sa_family=AF_INET6, sin6_port=htons(0), inet_pton(AF_INET6, "::ffff:127.0.0.1", &sin6_addr), sin6_flowinfo=0, sin6_scope_id=0}
    # {sa_family=AF_NETLINK, pid=0, groups=00000000}
    # {sa_family=AF_LOCAL, sun_path=@"xtables"}
    # {sa_family=AF_LOCAL, sun_path="/dev/log"}
    # {sa_family=AF_INET, sin_port=htons(6379), sin_addr=inet_addr("127.0.0.1")}
    sockaddr = dict()
    firsteq = argstr.find('=')
    firstcomma = argstr.find(',')
    family = argstr[firsteq+1:firstcomma]
    sockaddr['family'] = family
    argstr = argstr[firstcomma+2:]
    if family == 'AF_NETLINK' or family == 'AF_UNSPEC':
        pass
    elif family == 'AF_LOCAL':
        argstr = argstr[9:] # len('sun_path=') == 9
        if argstr[0] == '@':
            sockaddr['abstract'] = True
            argstr = argstr[1:]
        sockaddr['sun_path'], iscomplete, argstr = string_arg(argstr)
        assert iscomplete
    elif family == 'AF_INET' or family == 'AF_INET6':
        before = argstr.find('(')
        after = argstr.find(')')
        sockaddr['port'] = int(argstr[before+1:after])
        argstr = argstr[argstr.find('"', after):]
        sockaddr['addr'], iscomplete, argstr = string_arg(argstr)
        assert iscomplete
    else:
        raise Exception('Unhandled socket family', family)
    _, argstr = argstr.split('}', maxsplit=1)
    return sockaddr, argstr


class UnhandledSyscallError(Exception):
    pass


def make_at_handler(helper, name):
    def meth(self, argstr, ret, err):
        fd, argstr = fd_arg(argstr)
        argstr = next_arg(argstr)
        #cwd = self.cwd if fd == AT_FDCWD else self.fd2file[fd]
        cwd = self.cwd if fd == 'AT_FDCWD' else fd[1]
        helper(self, cwd, argstr, ret, err)
    meth.__name__ = name
    return meth

class ProcessImage(object):
    def __init__(self, parser):
        ''' expecting something like StraceParser '''
        self.exe = parser.exe
        self.argv = parser.argv
        self.envp = parser.envp
        self.cwd = parser.cwd
        self.exist_files = parser.exist_files
        self.written_files = parser.written_files
        self.children = parser.children
        self.connects = parser.connects
        self.binds = parser.binds
        self.exec_file = parser.exec_file

    def __repr__(self):
        return repr((self.exe, self.argv, self.envp, self.cwd,
            self.exist_files, self.written_files, self.children, self.connects,
            self.binds, self.exec_file))


class StraceParser(object):
    ''' Currently each instance represents one process
    '''
    def __init__(self, cwd, exe=None, argv=[], envp=[]):
        self.cwd = cwd
        self.inital_cwd = cwd
        self.exe = exe
        self.argv = argv
        self.envp = envp
        self.fd2file = {}
        self.exist_files = set([self.cwd]) # including directories, etc.
        self.written_files = set()
        self.children = []
        self.exec_file = None # the file used for execve
        self.exec_records = []
        self.connects = []
        self.binds = []

        self.handlers = {
                'open': self.sys_open,
                'stat': self.sys_unlink,
                'lstat': self.sys_unlink,
                'access': self.sys_access,
                'clone': self.sys_clone,
                'fork': self.sys_clone,
                'vfork': self.sys_clone,
                'execve': self.sys_execve,
                'truncate': self.sys_unlink,
                'chdir': self.sys_chdir,
                'fchdir': self.sys_fchdir,
                'rename': self.sys_rename,
                'mkdir': self.sys_mkdir,
                'rmdir': self.sys_rmdir,
                'creat': self.sys_creat,
                'link': self.sys_link,
                'unlink': self.sys_unlink,
                'symlink': self.sys_symlink,
                'readlink': self.sys_readlink,
                'chmod': self.sys_chmod,
                'chown': self.sys_chmod,
                'lchown': self.sys_chmod,
                'utime': self.sys_chmod,
                'mknod': self.sys_mkdir,
                # complex search paths; fortunately not used in glibc2
                'uselib': unhandled,
                'statfs': nop, #TODO python reads /selinux
                'pivot_root': unhandled,
                'chroot': self.sys_chroot,
                'acct': unhandled,
                'mount': unhandled,
                'umount2': unhandled,
                'swapon': unhandled,
                'swapoff': unhandled,
                'quotactl': unhandled,
                'setxattr': unhandled,
                'lsetxattr': unhandled,
                'getxattr': unhandled,
                'lgetxattr': unhandled,
                'listxattr': unhandled,
                'llistxattr': unhandled,
                'removexattr': unhandled,
                'lremovexattr': unhandled,
                'utimes': self.sys_chmod,
                'openat': self.sys_openat,
                'mkdirat': self.sys_mkdirat,
                'mknodat': self.sys_mkdirat,
                'fchownat': self.sys_fchownat,
                'futimesat': self.sys_fchownat,
                'newfstatat': self.sys_unlinkat, # no info on man or net; man fstatat
                'unlinkat': self.sys_unlinkat,
                'renameat': self.sys_renameat,
                'linkat': self.sys_linkat,
                'symlinkat': self.sys_symlinkat,
                'readlinkat': self.sys_readlinkat,
                'fchmodat': self.sys_fchownat,
                'faccessat': self.sys_faccessat,
                'utimensat': self.sys_fchownat,
                'fanotify_mark': unhandled,
                'name_to_handle_at': unhandled,
                'renameat2': unhandled, # not yet in my glibc

                'dup': nop,
                'dup2': nop,
                'dup3': nop,

                'sendfile': nop,
                'socket': nop,
                'connect': self.sys_connect,
                'accept': nop,
                'sendto': nop,
                'recvfrom': nop,
                'sendmsg': nop,
                'recvmsg': nop,
                'shutdown': nop,
                'bind': self.sys_bind,
                'listen': nop,
                'getsockname': nop,
                'getpeername': nop,
                'socketpair': nop,
                'setsockopt': nop,
                'getsockopt': nop,
                'accept4': nop,
                'recvmmsg': nop,
                'sendmmsg': nop,

                }

    def parse(self, file):
        for line in file:
            if line.startswith('---'):
                si_signo, si_code, pid = parse_signal(line.strip())
                if si_signo == 'SIGCHLD':
                    self.children.append((pid, self.cwd))
                continue
            if line.startswith('+++'):
                continue
            if line.rstrip().endswith('<detached ...>'): # last line...
                continue
            if line.startswith('????') and '<unfinished ...>' in line: #????( <unfinished ...>
                continue
            # print(line, end='')
            syscall, argstr, ret, retfdpath, err = parse_call(line)
            #print(argstr, ret, retfdpath, err)
            if syscall in self.handlers:
                # TODO retfdpath is not used yet
                self.handlers[syscall](argstr, ret, err)
        self.exec_records.append(ProcessImage(self))

    def helper_open0(self, cwd, filename, flags, ret, err):
        if err is None:
            # if 'O_CREAT' is given, only the dir must exist
            self.exist_files.add(os.path.join(cwd, filename))
            if 'O_CREAT' in flags:
                self.exist_files.add(os.path.join(cwd,
                    os.path.dirname(filename)))
            self.fd2file[ret] = os.path.join(cwd, filename)
            if 'O_CREAT' in flags or 'O_WRONLY' in flags or 'O_RDWR' in flags:
                self.written_files.add(os.path.join(cwd, filename))

    def helper_open1(self, cwd, argstr, ret, err):
        filename, iscomplete, argstr = string_arg(argstr)
        assert iscomplete
        argstr = next_arg(argstr)
        flags, _ = flags_arg(argstr)
        self.helper_open0(cwd, filename, flags, ret, err)

    def sys_open(self, argstr, ret, err):
        self.helper_open1(self.cwd, argstr, ret, err)

    def helper_access(self, cwd, argstr, ret, err):
        if err is None:
            pathname, iscomplete, _ = string_arg(argstr)
            assert iscomplete
            self.exist_files.add(os.path.join(cwd, pathname))

    def sys_access(self, argstr, ret, err):
        self.helper_access(self.cwd, argstr, ret, err)

    def sys_clone(self, argstr, ret, err):
        if err is None:
            self.children.append((ret, self.cwd))

    def sys_execve(self, argstr, ret, err):
        # we can hack json for our purposes as execve argstr should be valid
        #   json (except for top-level brackets). json will complain if the
        #   output is abbreviated or strings are incomplete
        if err is None:
            while True:
                try:
                    args = json.loads('[{}]'.format(argstr))
                    break
                except json.decoder.JSONDecodeError as e:
                    if e.msg == 'Invalid \\escape':
                        argstr = argstr[:e.pos] + '\\' + argstr[e.pos:]
                    else:
                        raise
            
            filename = args[0] # this is abs path
            argv = args[1]
            envp = args[2]
            self.exec_file = filename
            # FIXME(huaifeng): 
            # ProcessImage seems like that each exe has its own files/envs. But according to the code, it's not the case.
            self.exec_records.append(ProcessImage(self))
            
            # reset members
            self.exe = filename
            self.argv = argv
            self.envp = envp
            self.exist_files = set([self.cwd, filename])
            self.written_files = set()
            self.children = []
            self.exec_file = None # the file used for execve
            self.connects = []
            self.binds = []

    def sys_chdir(self, argstr, ret, err):
        if err is None:
            path, iscomplete, _ = string_arg(argstr)
            assert iscomplete
            self.cwd = os.path.join(self.cwd, path)

    def sys_chroot(self, argstr, ret, err):
        # this is unhandled
        if err is None:
            path, iscomplete, _ = string_arg(argstr)
            assert iscomplete
            print('chroot', path)

    def sys_fchdir(self, argstr, ret, err):
        if err is None:
            fd, _ = fd_arg(argstr)
            # self.cwd = self.fd2file[fd]
            self.cwd = fd[1] # we get direct info from strace -y

    def sys_rename(self, argstr, ret, err):
        if err is None:
            path1, iscomplete, argstr = string_arg(argstr)
            assert iscomplete
            argstr = next_arg(argstr)
            path2, iscomplete, _ = string_arg(argstr)
            assert iscomplete
            # actually, more may be said after seeing the err no, but not
            #   saying now
            self.exist_files.add(os.path.join(self.cwd, path1))
            self.exist_files.add(os.path.join(self.cwd,
                os.path.dirname(path2)))
            self.written_files.add(os.path.join(self.cwd, path2))

    def helper_mkdir(self, cwd, argstr, ret, err):
        pathname, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        if err is None:
            self.exist_files.add(os.path.join(cwd,
                os.path.dirname(pathname)))
            self.written_files.add(os.path.join(cwd, pathname))
        elif err == 'EEXIST':
            self.exist_files.add(os.path.join(cwd, pathname))

    def sys_mkdir(self, argstr, ret, err):
        self.helper_mkdir(self.cwd, argstr, ret, err)

    def sys_rmdir(self, argstr, ret, err):
        pathname, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        if err is None or err == 'EBUSY' or err == 'ENOTEMPTY':
            self.exist_files.add(os.path.join(self.cwd, pathname))

    def sys_creat(self, argstr, ret, err):
        filename, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        # based on man page, this is almost same as open
        self.helper_open0(self.cwd, filename, CREAT_FLAGS, ret, err)

    def sys_link(self, argstr, ret, err):
        # treat as rename. There are some differences in the semantics, such as
        # newpath is not overwritten in link, but ignoring for now
        # For example, EEXIST should be handled
        self.sys_rename(argstr, ret, err)

    def helper_unlink(self, cwd, argstr, ret, err):
        pathname, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        if err is None:
            self.exist_files.add(os.path.join(cwd, pathname))

    def sys_unlink(self, argstr, ret, err):
        self.helper_unlink(self.cwd, argstr, ret, err)

    def sys_symlink(self, argstr, ret, err):
        pathname, iscomplete, argstr = string_arg(argstr)
        assert iscomplete
        argstr = next_arg(argstr)
        newpath, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        if err is None:
            self.exist_files.add(os.path.join(self.cwd,
                os.path.dirname(pathname)))
            self.written_files.add(os.path.join(self.cwd, newpath))
        if err == 'EEXIST':
            self.exist_files.add(os.path.join(self.cwd, newpath))

    def sys_readlink(self, argstr, ret, err):
        # similar semantics to unlink without checking error nums. Note EINVAL
        # alone does not mean the file exists.
        self.sys_unlink(argstr, ret, err)

    def helper_chmod(self, cwd, argstr, ret, err):
        pathname, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        # we have seen cases where utimensat call has NULL paths
        if err is None and pathname is not None:
            self.exist_files.add(os.path.join(cwd, pathname))
            self.written_files.add(os.path.join(cwd, pathname))

    def sys_chmod(self, argstr, ret, err):
        # similar to unlink
        self.helper_chmod(self.cwd, argstr, ret, err)

    sys_openat = make_at_handler(helper_open1, 'sys_openat')
    sys_mkdirat = make_at_handler(helper_mkdir, 'sys_mkdirat')
    sys_unlinkat = make_at_handler(helper_unlink, 'sys_unlinkat')
    sys_fchownat = make_at_handler(helper_chmod, 'sys_fchownat')

    def sys_renameat(self, argstr, ret, err):
        if err is None:
            fd, argstr = fd_arg(argstr)
            #cwd1 = self.cwd if fd == AT_FDCWD else self.fd2file[fd]
            cwd1 = self.cwd if fd == 'AT_FDCWD' else fd[1]
            argstr = next_arg(argstr)
            path1, iscomplete, argstr = string_arg(argstr)
            assert iscomplete
            argstr = next_arg(argstr)
            fd, argstr = fd_arg(argstr)
            #cwd2 = self.cwd if fd == AT_FDCWD else self.fd2file[fd]
            cwd2 = self.cwd if fd == 'AT_FDCWD' else fd[1]
            argstr = next_arg(argstr)
            path2, iscomplete, argstr = string_arg(argstr)
            assert iscomplete
            self.exist_files.add(os.path.join(cwd1, path1))
            self.exist_files.add(os.path.join(cwd2, os.path.dirname(path2)))
            self.written_files.add(os.path.join(cwd2, path2))

    def sys_linkat(self, argstr, ret, err):
        self.sys_renameat(argstr, ret, err)

    def sys_symlinkat(self, argstr, ret, err):
        _, iscomplete, argstr = string_arg(argstr)
        assert iscomplete
        argstr = next_arg(argstr)
        fd, argstr = fd_arg(argstr)
        #cwd = self.cwd if fd == AT_FDCWD else self.fd2file[fd]
        cwd = self.cwd if fd == 'AT_FDCWD' else fd[1]
        argstr = next_arg(argstr)
        newpath, iscomplete, _ = string_arg(argstr)
        assert iscomplete
        if err is None:
            self.exist_files.add(os.path.join(cwd, os.path.dirname(newpath)))
            self.written_files.add(os.path.join(cwd, newpath))
        if err == 'EEXIST':
            self.exist_files.add(os.path.join(cwd, newpath))

    sys_readlinkat = make_at_handler(helper_unlink, 'sys_readlinkat')
    sys_faccessat = make_at_handler(helper_access, 'sys_faccessat')

    # not using it for now; we have strace -y at least for files
    def sys_dup(self, argstr, ret, err):
        fd, _ = fd_arg(argstr)
        if err is None and fd[0] in fd2file:
            fd2file[ret] = fd2file[fd];

    def sys_connect(self, argstr, ret, err):
        # Note: we are not worring about TCP/UDP for now
        if err is None:
            fd, argstr = fd_arg(argstr)
            argstr = next_arg(argstr)
            sockaddr, _ = sockaddr_arg(argstr)
            if sockaddr['family'] != 'AF_UNSPEC':
                self.connects.append(sockaddr)

    def sys_bind(self, argstr, ret, err):
        # Note: we are not worring about TCP/UDP for now
        if err is None:
            fd, argstr = fd_arg(argstr)
            argstr = next_arg(argstr)
            sockaddr, _ = sockaddr_arg(argstr)
            self.binds.append(sockaddr)


class StraceParserContainerRoot(StraceParser):
    def __init__(self, cwd):
        StraceParser.__init__(self, cwd)
        self.orig_handlers = self.handlers
        self.handlers = {'pivot_root': self.sys_pivotroot}

    def sys_pivotroot(self, argstr, ret, err):
        # we assume only one pivot_root will happen
        # reset some key structures
        # self.cwd = '/' # chdir to '/' is recommended as next syscall
        # keep using the cwd that we were passed
        self.fd2file = {}
        # finally mark the handler as unhandled
        self.handlers = {'execve': self.sys_execve_2}

    def sys_execve_2(self, argstr, ret, err):
        self.handlers = self.orig_handlers
        self.handlers['execve'](argstr, ret, err)
        self.exec_records.pop() # the record before the first execve is useless

        
def process(rootpid, trace_log_file, cwd='/', iscontainerroot=True):
    ''' rootpid is typically the original pid we started stracing
        traces_prefix is the argument of -o option, e.g., /tmp/all.strace
    '''
    rootparser = (StraceParserContainerRoot(cwd) if iscontainerroot else
            StraceParser(cwd))
    parsers = {rootpid: rootparser}

    
    with open(trace_log_file) as f:
        parsers[rootpid].parse(f)

    # the following code exist bugs. It assumes that the strace log files end with
    # pid. But according to the doc. The strace log file name is customized by users.
    """
    def parse_helper(pid, parser):
        trace = '{}.{}'.format(traces_prefix, pid)
        if trace not in trace_files:
            return
        assert trace in trace_files, trace
        with open(trace) as f:
            parser.parse(f)
    parse_helper(rootpid, parsers[rootpid])
    """


    return parsers
