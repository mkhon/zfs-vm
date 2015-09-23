#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import subprocess
import pipes
import collections
from copy import copy

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

class Snapshot:
    """Filesystem snapshot"""
    def __init__(self, name):
        self.name = name            # snapshot name
        self.guid = None            # snapshot guid
        self.createtxg = 0          # snapshot create txn

class Streamlines(dict):
    """dict of streamlines (key: name)"""
    def __init__(self):
        self.snapshots = {}         # guid -> Streamline

class Streamline:
    """Streamline is a filesystem with snapshots"""
    def __init__(self, name, origin):
        self.name = name            # filesystem name
        self.origin = origin        # origin snapshot
        self.parent = None          # parent (origin) streamline
        self.snapshots = {}         # streamline snapshots
        self.processed = False

    @staticmethod
    def get(host):
        """fetch streamlines from host
:param host: host to fetch from (None if localhost)
:type host: str
:returns: streamlines on specified host
:rtype: dict of Streamlines (by name)"""

        # get filesystem origins
        origins = {}
        for l in runcmd(host, "zfs", "get", "-H", "-p", "-o", "name,property,value", "-t", "filesystem", "origin").split("\n"):
            # pool/vm/Root3   origin  pool/vm/Root2@zfs-vm:foo:6
            if not l:
                continue
            debug(l)
            (name, prop, value) = l.split("\t")
            if value == "-":
                continue    # empty value

            origins[name] = value

        # get streamlines
        streamlines = Streamlines()
        for l in runcmd(host, "zfs", "get", "-H", "-p", "-o", "name,property,value", "-t", "snapshot", "guid,createtxg").split("\n"):
            # pool/src/OpenVZ@pool-src-OpenVZ-20150529-Initial  createtxg   1379    -
            if not l:
                continue
            debug(l)
            (name, propname, value) = l.split("\t")
            if value == "-":
                continue    # empty value

            fs = name.split("@")[0]
            if streamlines.get(fs) is None:
                streamlines[fs] = Streamline(fs, origins.get(fs))
            s = streamlines[fs]
            if propname == "guid":
                guid = value
                s.snapshots[guid] = Snapshot(name)
            setattr(s.snapshots[guid], propname, value)

        # sort snapshots by "createtxg"
        for s in streamlines.itervalues():
            if s.origin:
                s.parent = streamlines.get(s.origin.split("@")[0])
            s.snapshots = collections.OrderedDict(
                sorted(s.snapshots.items(), key=lambda x: int(x[1].createtxg)))
            for snap in s.snapshots.itervalues():
                streamlines.snapshots[snap.guid] = s

        return streamlines

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
    try:
        opts, args = getopt.getopt(args, "n:p")
    except getopt.GetoptError as err:
        usage(cmd_list, err)
    name, list_parents = None, False
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-p":
            list_parents = True

    def list_streamline(s):
        if s.processed:
            return
        if s.parent is not None and list_parents:
            list_streamline(s.parent)

        # print streamline
        l = s.name
        if s.origin is not None:
            l += " (origin: {0})".format(s.origin)
        print(l)

        # print snapshots
        for snap in s.snapshots.itervalues():
            l = "\t{0}".format(snap.name)
            if use_verbose:
                l += " (createtxg: {0}, guid: {1})".format(snap.createtxg, snap.guid)
            print(l)

        s.processed = True

    streamlines = Streamline.get(None if len(args) < 1 else args[0])
    for s in sorted(streamlines.values(), key=lambda x: x.name):
        if name is not None and s.name != name:
            continue
        list_streamline(s)
cmd_list.usage = "list [-n name] [-p] [[user@]host]"
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
