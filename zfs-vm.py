#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import re
import subprocess

# constants
VM_PROPERTY_STREAMLINE = "vm:streamline"

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

class Streamline:
    def __init__(self, name):
        self.name = name
        self.versions = []

    @staticmethod
    def get(host):
        """fetch streamlines from host
:param host: host to fetch from (None if localhost)
:type host: str
:returns: streamlines on specified host
:rtype: dict of Streamlines (by name)"""
        streamlines = {}

        for l in runcmd(host, "zfs", "get", "-H", "-t", "snapshot", "-o", "name,value", VM_PROPERTY_STREAMLINE).split("\n"):
            try:
                name, value = l.split("\t")
            except ValueError:
                continue
            if value == "-":
                continue
            debug("{0}".format(l))
            try:
                fs, snapshot = name.split("@")
            except ValueError:
                print("Warning: Invalid snapshot name {0} (missing '@')".format(name))
                continue
            try:
                name, version = snapshot.split(":")
            except ValueError:
                print("Warning: Invalid snapshot version {0} (missing ':')".format(snapshot))
                continue
            s = streamlines.get(name)
            if s is None:
                s = streamlines[name] = Streamline(name)
            s.versions += [version]
            debug("{host}: {name}:{version}".format(host="local" if host is None else host, name=name, version=version))
        return streamlines

    def pull(self, host, ls):
        """pull remote streamline to local
:param host: host to pull from (None for localhost)
:type host: str
:param ls: local streamline (can be None)
:type ls: Streamline"""
        cmd = hostcmd(self.host, "zfs", "send")
        if ls is not None:
            # TODO: compute incremental versions to pull
            pass
        cmd += ["|", "zfs", "receive"]
        debug("pull: {0}".format(' '.join(cmd)))

    def push(self, host, fs, rs):
        """push local streamline to remote
:param host: host to push to (None for localhost)
:type host: str
:param fs: filesystem to put snapshot to
:type fs: str
:param rs: remote streamline (can be None)
:type rs: Streamline"""
        cmd = ["zfs", "send"]
        if rs is not None:
            # TODO: compute incremental versions to push
            pass
        cmd += ["|"]
        cmd += hostcmd(host, "zfs", "receive")
        debug("push: {0}".format(' '.join(cmd)))

###########################################################################
# commands
def cmd_list(args):
    """list command"""
    debug("list {0}".format(args))
    for name, s in sorted(Streamline.get(None if len(args) < 1 else args[0]).iteritems()):
        for v in sorted(s.versions, key=int):
            print("{name}:{version}".format(name=name, version=v))
cmd_list.usage = "list [[<user>@]<host>]"
commands["list"] = cmd_list

def cmd_tag(args):
    """tag command"""
    debug("tag {0}".format(args))
    if len(args) < 2:
        usage(cmd_tag)
    version = args[0]
    fs = args[1]
cmd_tag.usage = "tag [<name>:]<version> <filesystem|container-id>"
commands["tag"] = cmd_tag

def parse_remote(remote):
    """parse remote specification
:param remote: remote specification ([[<user>@]<host>:][fs])
:type remote: str
:returns: parsed host and fs
"""
    m = re.match(r"((.*):)?([^:]+)?", remote)
    if m is None:
        print("Error: Invalid remote specification", file=sys.stderr)
        sys.exit(1)
    host = m.group(2)
    fs = m.group(3)
    return host, fs

def cmd_pull(args):
    """pull command"""
    debug("pull {0}".format(args))
    if len(args) < 1:
        usage(cmd_pull)
    remote_host, remote_fs = parse_remote(args[0])
    name = None if len(args) < 2 else args[1]
    debug("remote_host: {0}, remote_fs: {1}, name: {2}".format(remote_host, remote_fs, name))

    local_streamlines = Streamline.get(None)
    for s in Streamline.get(remote_host).itervalues():
        if name is not None and s.name != name:
            continue
        # TODO: filter on remote_fs
        s.pull(remote_host, local_streamlines.get(s.name))
cmd_pull.usage = "pull [[<user>@]<host>:][fs] [name]"
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {0}".format(args))
    if len(args) < 1:
        usage(cmd_push)
    remote_host, remote_fs = parse_remote(args[0])
    name = None if len(args) < 2 else args[1]
    debug("remote_host: {0}, remote_fs: {1}, name: {2}".format(remote_host, remote_fs, name))
    if remote_fs is None:
        usage(cmd_push)

    remote_streamlines = Streamline.get(remote_host)
    for s in Streamline.get(None).itervalues():
        if name is not None and s.name != name:
            continue
        s.push(remote_host, remote_fs, remote_streamlines.get(s.name))
cmd_push.usage = "push [[<user>@]<host>:]<fs> [name]"
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
