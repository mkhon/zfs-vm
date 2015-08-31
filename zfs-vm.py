#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import re
import subprocess
import pipes
from copy import copy

# constants
ZFS_VM_PREFIX = "zfs-vm"

# globals
commands = {}
use_sudo = False
use_debug = False

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
    debug("hostcmd: {0}".format(' '.join(cmd)))
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

class Streamline:
    def __init__(self, fs, name):
        self.fs = fs
        self.name = name
        self.versions = []

    @staticmethod
    def _snapshot_name(fs, name, version):
        return "{fs}@{prefix}:{name}:{version}".format(fs=fs, prefix=ZFS_VM_PREFIX, name=name, version=version)

    def snapshot_name(self, version):
        return self._snapshot_name(self.fs, self.name, version)

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
                s = streamlines[name] = Streamline(fs, name)
            s.versions += [version]
            debug("{host}: {name}:{version}".format(host="local" if host is None else host, name=name, version=version))
        return streamlines

    @staticmethod
    def find_source(a, b):
        """find highest common version from a in b
:type a: Streamline
:type b: Streamline
:returns: highest common version from a in b
:rtype: str"""
        common_ver = None
        for v in reversed(a.versions):
            if v in b.versions:
                common_ver = v
                break
        return common_ver

    def pull(self, remote_host, local_fs, local):
        """pull remote streamline to local
:param remote_host: host to pull from (None for localhost)
:type remote_host: str
:param remote_fs: filesystem to put snapshot to
:type remote_fs: str
:param local: local streamline (can be None)
:type local: Streamline"""
        if not self.versions:
            debug("empty version list")
            return

        self.versions.sort(key=int)
        debug("self.versions: {0}".format(self.versions))

        cmd = hostcmd(remote_host, "zfs", "send", "-p")
        if use_debug:
            cmd += ["-v"]   # verbose

        if local is None:
            # pull base version
            debug("no local versions, pulling base version {0}".format(self.versions[0]))
            cmd_base = copy(cmd)
            cmd_base += [ self.snapshot_name(self.versions[0]) ]
            cmd_base += ["|"]
            cmd_base += hostcmd(None, "zfs", "recv", "-u", "-F", os.path.join(local_fs, self.name))
            runshell(*cmd_base)

            local = Streamline(os.path.join(local_fs, self.name), self.name)
            local.versions += self.versions[0]
        else:
            # override local fs
            local_fs = os.path.dirname(local.fs)

            local.versions.sort(key=int)
            debug("local.versions: {0}".format(local.versions))

        # find highest common version
        common_ver = self.find_source(self, local)
        if common_ver is None:
            print("No common versions found")
            return
        elif common_ver == self.versions[-1]:
            print("All up-to-date")
            return

        cmd += ["-I", self.snapshot_name(common_ver), self.snapshot_name(self.versions[-1]) ]
        cmd += ["|"]
        cmd += hostcmd(None, "zfs", "recv", "-u", os.path.join(local_fs, self.name))
        runshell(*cmd)

    def push(self, remote_host, remote_fs, remote):
        """push local streamline to remote
:param remote_host: host to push to (None for localhost)
:type remote_host: str
:param remote_fs: filesystem to put snapshot to
:type remote_fs: str
:param remote: remote streamline (can be None)
:type remote: Streamline"""
        if not self.versions:
            debug("empty version list")
            return

        self.versions.sort(key=int)
        debug("self.versions: {0}".format(self.versions))

        cmd = hostcmd(None, "zfs", "send", "-p")
        if use_debug:
            cmd += ["-v"]   # verbose

        if remote is None:
            # push base version
            debug("no remote versions, pushing base version {0}".format(self.versions[0]))
            cmd_base = copy(cmd)
            cmd_base += [ self.snapshot_name(self.versions[0]) ]
            cmd_base += ["|"]
            cmd_base += hostcmd(remote_host, "zfs", "recv", "-u", "-F", os.path.join(remote_fs, self.name))
            runshell(*cmd_base)

            remote = Streamline(os.path.join(remote_fs, self.name), self.name)
            remote.versions += self.versions[0]
        else:
            remote.versions.sort(key=int)
            debug("remote.versions: {0}".format(remote.versions))

        # find highest common version
        common_ver = self.find_source(self, remote)
        if common_ver is None:
            print("No common versions found")
            return
        elif common_ver == self.versions[-1]:
            print("Everything up-to-date")
            return

        cmd += ["-I", self.snapshot_name(common_ver), self.snapshot_name(self.versions[-1]) ]
        cmd += ["|"]
        cmd += hostcmd(remote_host, "zfs", "recv", "-u", os.path.join(remote_fs, self.name))
        runshell(*cmd)

###########################################################################
# commands
def cmd_list(args):
    """list command"""
    debug("list {0}".format(args))
    streamlines = Streamline.get(None if len(args) < 1 else args[0])
    for name in sorted(streamlines.iterkeys()):
        s = streamlines[name]
        for v in sorted(s.versions, key=int):
            print(s.snapshot_name(v))
cmd_list.usage = "list [[user@]host]"
commands["list"] = cmd_list

def cmd_tag(args):
    """tag command"""
    debug("tag {0}".format(args))
    try:
        opts, args = getopt.getopt(args, "n:")
    except getopt.GetoptError as err:
        print(str(err), file=sys.stderr)
        usage(cmd_tag)
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
                        version = int(s.versions[-1]) + 1
                        debug("using version {0} (last version {1})".format(version, s.versions[-1]))
    if name is None or version is None:
        print("""Error: Failed to detect streamline name and version from filesystem {0}
Please specify streamline name with -n option
""".format(fs), file=sys.stderr)
        usage(cmd_tag)

    cmd = ["zfs", "snapshot", Streamline._snapshot_name(fs, name, version)]
    runshell(*cmd)

cmd_tag.usage = "tag [-n [name:]version] <filesystem|container-id>"
commands["tag"] = cmd_tag

def cmd_pull(args):
    """pull command"""
    debug("pull {0}".format(args))
    if len(args) < 2:
        usage(cmd_pull)
    remote_host = args[0] if args[0] != "local" else None
    if args[1][-1] == "/":
        local_fs = args[1]
        name = None
    else:
        local_fs = os.path.dirname(args[1])
        name = os.path.basename(args[1])
    debug("remote_host: {0}, local_fs: {1}, name: {2}".format(remote_host, local_fs, name))

    local_streamlines = Streamline.get(None)
    for s in Streamline.get(remote_host).itervalues():
        if name is not None and s.name != name:
            continue
        s.pull(remote_host, local_fs, local_streamlines.get(s.name))
cmd_pull.usage = "pull <[user@]host | local> <fs/[name]>"
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {0}".format(args))
    if len(args) < 1:
        usage(cmd_push)
    m = re.match(r"((.*):)?([^:]+)", args[0])
    if m is None:
        print("Error: Invalid remote specification\n", file=sys.stderr)
        usage(cmd_push)
    remote_host = m.group(2)
    remote_fs = m.group(3)
    name = None if len(args) < 2 else args[1]
    debug("remote_host: {0}, remote_fs: {1}, name: {2}".format(remote_host, remote_fs, name))

    remote_streamlines = Streamline.get(remote_host)
    for s in Streamline.get(None).itervalues():
        if name is not None and s.name != name:
            continue
        s.push(remote_host, remote_fs, remote_streamlines.get(s.name))
cmd_push.usage = "push <[[user@]host:]fs> [name]"
commands["push"] = cmd_push

def usage(cmd=None):
    """show usage and exit
:param cmd: command to show usage for (None - show command list)
:type cmd: command function"""
    name = os.path.basename(sys.argv[0])
    if cmd is None:
        print("""Usage: {name} [-s] <command> [args...]

Options:
-s	use sudo when executing remote commands

Commands:""".format(name=name), file=sys.stderr)
        for c in sorted(commands.iterkeys()):
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
        opts, args = getopt.getopt(args[1:], "dhs")
    except getopt.GetoptError as err:
        print(str(err), file=sys.stderr)
        usage()

    global use_debug, use_sudo
    for o, a in opts:
        if o == "-d":
            use_debug = True
        elif o == "-h":
            usage()
        elif o == "-s":
            use_sudo = True

    if len(args) < 1:
        usage()
    do_fun = commands.get(args[0])
    if do_fun is None:
        usage()
    do_fun(args[1:])

if __name__ == "__main__":
    main(sys.argv)

# vi: ts=4:sw=4:et:
