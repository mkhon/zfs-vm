#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import re
import subprocess
import pipes
import collections
from copy import copy

# constants
ZFS_VM_PREFIX = "zfs-vm"

# globals
commands = {}
use_sudo = False
use_debug = False
use_verbose = False

def debug(s):
    """print debug message
:param s: debug message string
:type s: str"""
    if use_debug:
        print(">> DEBUG: {0}".format(s))

def hostcmd(host, *args):
    """generate command to be run on host
:param host: host to run command on (None for localhost)
:type host: str
:returns: command stdout
:rtype: str"""
    cmd = []
    if host is not None:
        cmd += ["ssh", host]
        if use_sudo:
            cmd += ["sudo"]
    cmd += args
    return cmd

def runcmd(host, *args):
    """run command on host
:param host: host to run command on (None for localhost)
:type host: str
:returns: command stdout
:rtype: str"""
    try:
        output = subprocess.check_output(hostcmd(host, *args))
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {0}".format(err.returncode), file=sys.stderr)
        exit(1)
    return output

def runshell(*args):
    """run command through shell"""
    cmd = ' '.join(map(lambda x:
        x if len(x) == 1 and x in "&|><" or x == ">>" else pipes.quote(x),
        args))
    debug("runshell: {0}".format(cmd))
    try:
        subprocess.check_call(cmd, shell=True)
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {0}".format(err.returncode), file=sys.stderr)
        exit(1)

#
# Iterate over possible incremental snapshot versions
#
# If the versions in src are:
# fs1@1, fs1@2, fs1@3, fs1@4, fs2@5, fs2@6, fs2@7, fs3@8, fs3@9
# and start_ver is 2 the iterator returns
# - fs1@4 (last version in fs1)
# - fs2@5 (first version in fs2)
# - fs2@7 (last version in fs2)
# - fs3@8 (first version in fs3)
# - fs3@9 (last version in fs3)
class VersionIterator:
    def __init__(self, s, start_ver):
        """Initialize version iterator
:param s: streamline to iterate over
:type s: Streamline
:param start_ver: start version
:type start_ver: str"""
        self.s = s
        self.start_ver = start_ver
        self.versions = []
        self.i = None
        self.prev_ver = None
        self.next_ver = None
 
    def __iter__(self):
        return self

    def next(self):
        if self.i is None:
            # initialize: find start position
            self.i = self.s.versions.iterkeys()
            while True:
                self.prev_ver = self.i.next()
                if self.prev_ver == self.start_ver:
                    self.next_ver = self.i.next()
                    break

        if len(self.versions) == 0:
            # find more versions
            if self.next_ver is None:
                raise StopIteration

            while True:
                try:
                    if self.s.versions[self.prev_ver] != self.s.versions[self.next_ver]:
                        # different fs found
                        if self.prev_ver != self.start_ver:
                            self.versions.append(self.prev_ver)
                        self.versions.append(self.next_ver)

                        self.start_ver = self.prev_ver = self.next_ver
                        self.next_ver = self.i.next()
                        break

                    self.prev_ver = self.next_ver
                    self.next_ver = self.i.next()
                except StopIteration:
                    if self.next_ver != self.start_ver:
                        self.versions.append(self.next_ver)
                    self.next_ver = None
                    break

        if len(self.versions) == 0:
            raise StopIteration
        return self.versions.pop(0)

class Streamline:
    def __init__(self, name):
        self.name = name
        self.versions = {}

    def first_version(self):
        """get first version number"""
        return self.versions.iterkeys().next()

    def last_version(self):
        """get last version number"""
        return reversed(self.versions.keys()).next()

    def version_fs(self, parent_fs, version):
        """build version fs name"""
        if parent_fs is not None:
            return os.path.join(parent_fs, os.path.basename(self.versions[version]))
        return self.versions[version]

    @staticmethod
    def snapshot_name(fs, name, version):
        return "{fs}@{prefix}:{name}:{version}".format(fs=fs, prefix=ZFS_VM_PREFIX, name=name, version=version)

    def version_snapshot(self, v):
        return self.snapshot_name(self.versions[v], self.name, v)

    @staticmethod
    def parse_name(snapshot):
        """parse streamline name and version from snapshot name
:param snapshot: snapshot name
:type snapshot: str
:returns: a tuple (fs, name, version) or None if not a zfs-vm snapshot"""
        try:
            fs, snapname = snapshot.split("@")
        except ValueError:
            print("Warning: Invalid snapshot name {0} (missing '@')".format(snapshot))
            return None
        try:
            prefix, name, version = snapname.split(":")
        except ValueError:
            debug("Not a zfs-vm snapshot {0} (missing ':' in snapname)".format(snapshot))
            return None
        if prefix != ZFS_VM_PREFIX:
            debug("Not a zfs-vm snapshot {0} (prefix is not {1})".format(snapshot, ZFS_VM_PREFIX))
            return None
        if not version.isdigit():
            debug("Not a zfs-vm snapshot {0} (version is not a number)".format(snapshot))
            return None
        return fs, name, version

    @staticmethod
    def get(host):
        """fetch streamlines from host
:param host: host to fetch from (None if localhost)
:type host: str
:returns: streamlines on specified host
:rtype: dict of Streamlines (by name)"""
        streamlines = {}

        for l in runcmd(host, "zfs", "list", "-H", "-t", "snapshot", "-o", "name").split("\n"):
            if not l:
                continue
            debug(l)
            n = Streamline.parse_name(l)
            if n is None:
                continue
            (fs, name, version) = n
            s = streamlines.get(name)
            if s is None:
                s = streamlines[name] = Streamline(name)
            s.versions[version] = fs
        for s in streamlines.itervalues():
            s.versions = collections.OrderedDict(sorted(s.versions.items(), key=lambda x: int(x[0])))
        return streamlines

    def __find_common(self, s):
        """find highest common version with b
:type b: Streamline
:returns: highest common version or None
:rtype: str"""
        return None

    def sync(self, s, send_host, recv_host, recv_parent_fs):
        if not self.versions:
            debug("empty version list")
            return
        debug("self.versions: {0}".format(self.versions))

        cmd = hostcmd(send_host, "zfs", "send", "-p", "-P")
        if use_verbose:
            cmd += ["-v"]

        # push base version if no versions on receiver
        if s is None:
            base_ver = self.first_version()
            debug("syncing base version {0}".format(base_ver))
            cmd_base = copy(cmd)
            cmd_base += [self.version_snapshot(base_ver)]
            cmd_base += ["|"]
            cmd_base += hostcmd(recv_host, "zfs", "recv")
            if use_verbose:
                cmd_base += ["-v"]
            cmd_base += [self.version_fs(recv_parent_fs, base_ver)]

            runshell(*cmd_base)

            s = Streamline(self.name)
            s.versions[base_ver] = self.version_fs(recv_parent_fs, base_ver)
        else:
            debug("versions: {0}".format(s.versions))

        # find highest common version
        for v in reversed(self.versions):
            if v in s.versions:
                start_ver = v
                break
        else:
            print("No common versions found")
            return

        # check if nothing to do
        last_ver = self.last_version()
        if start_ver == last_ver:
            print("Everything up-to-date")
            return

        # perform incremental send/recv
        for next_ver in VersionIterator(self, start_ver):
            inc_cmd = copy(cmd)
            inc_cmd += ["-I", self.version_snapshot(start_ver), self.version_snapshot(next_ver)]
            inc_cmd += ["|"]
            inc_cmd += hostcmd(recv_host, "zfs", "recv")
            if use_verbose:
                inc_cmd += ["-v"]
            inc_cmd += [self.version_fs(recv_parent_fs, next_ver)]
            runshell(*inc_cmd)
            start_ver = next_ver

def do_sync(cmd, args):
    try:
        opts, args = getopt.getopt(args, "n:d:")
    except getopt.GetoptError as err:
        usage(cmd, err)
    name, parent_fs = None, None
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-d":
            parent_fs = a
    if len(args) < 1:
        usage(cmd_pull)
    remote_host = args[0] if args[0] != "local" else None
    debug("remote_host: {0}, parent_fs: {1}, name: {2}".format(remote_host, parent_fs, name))

    if cmd == cmd_push:
        send_host = None
        recv_host = remote_host
    else:
        send_host = remote_host
        recv_host = None

    recv_streamlines = Streamline.get(recv_host)
    for s in Streamline.get(send_host).itervalues():
        if name is not None and s.name != name:
            continue
        s.sync(recv_streamlines.get(s.name), send_host, recv_host, parent_fs)

###########################################################################
# commands
def cmd_list(args):
    """list command"""
    debug("list {0}".format(args))
    streamlines = Streamline.get(None if len(args) < 1 else args[0])
    for name in sorted(streamlines):
        s = streamlines[name]
        for v in s.versions:
            print(s.version_snapshot(v))
cmd_list.usage = "list [[user@]host]"
commands["list"] = cmd_list

def cmd_pull(args):
    """pull command"""
    debug("pull {0}".format(args))
    do_sync(cmd_pull, args)
cmd_pull.usage = "pull [-n name] [-d local-dest-fs] [user@]host"
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {0}".format(args))
    do_sync(cmd_push, args)
cmd_push.usage = "push [-n name] [-d remote-dest-fs] [user@]host"
commands["push"] = cmd_push

def cmd_tag(args):
    """tag command"""
    debug("tag {0}".format(args))
    try:
        opts, args = getopt.getopt(args, "n:")
    except getopt.GetoptError as err:
        usage(cmd_tag, err)
    version = None
    for o, a in opts:
        if o == "-n":
            version = a
    if len(args) < 1:
        usage(cmd_tag)
    fs = args[0]

    name = None
    if version is not None:
        try:
            name, version = version.split(":")
        except ValueError:
            pass
    if name is None:
        # try to detect name/version from fs
        cmd = ["zfs", "get", "-H", "-o", "value", "origin", fs]
        origin = runcmd(None, *cmd).split("\n")[0]
        debug("filesystem {0}: origin {1}".format(fs, origin))
        if origin == "-":
            name = None
        else:
            n = Streamline.parse_name(origin)
            if n is not None:
                name = n[1]
                debug("using streamline name {0}".format(name))
                if version is None:
                    local_streamlines = Streamline.get(None)
                    s = local_streamlines.get(name)
                    if s is not None:
                        last_ver = s.last_version()
                        version = int(last_ver) + 1
                        debug("using version {0} (last version {1})".format(version, last_ver))
    if name is None or version is None:
        usage(cmd_tag, """Failed to detect streamline name and version from filesystem {0}
Please specify streamline name with -n option""".format(fs))

    snapshot_name = Streamline.snapshot_name(fs, name, version)
    cmd = ["zfs", "snapshot", snapshot_name]
    runshell(*cmd)
    print("Tagged {0}".format(snapshot_name))

cmd_tag.usage = "tag [-n [name:]version] filesystem|container-id"
commands["tag"] = cmd_tag

def usage(cmd=None, error=None):
    """show usage and exit
:param cmd: command to show usage for (None - show command list)
:type cmd: command function"""
    if error is not None:
        print("Error: {0}\n".format(error), file=sys.stderr)

    name = os.path.basename(sys.argv[0])
    if cmd is None:
        print("""Usage: {name} [-d] [-s] <command> [args...]

Options:
-d  debug
-v  verbose send/recv
-s	use sudo when executing remote commands

Commands:""".format(name=name), file=sys.stderr)
        for c in sorted(commands):
            print("{usage}".format(name=name, usage=commands[c].usage))
    else:
        print("Usage: {name} {usage}".format(name=name, usage=cmd.usage))
    sys.exit(1)

###########################################################################
# main function
def main(args):
    """main function"""
    # parse command-line options
    try:
        opts, args = getopt.getopt(args[1:], "dhsv")
    except getopt.GetoptError as err:
        usage(error=err)

    global use_sudo, use_debug, use_verbose
    for o, a in opts:
        if o == "-d":
            use_debug = True
        elif o == "-h":
            usage()
        elif o == "-s":
            use_sudo = True
        elif o == "-v":
            use_verbose = True

    if len(args) < 1:
        usage()
    do_fun = commands.get(args[0])
    if do_fun is None:
        usage()
    do_fun(args[1:])

if __name__ == "__main__":
    main(sys.argv)

# vi: ts=4:sw=4:et:
