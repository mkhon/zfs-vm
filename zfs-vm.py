#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import subprocess
import pipes
import collections
import json
from sets import Set

# globals
commands = {}
use_sudo = False
use_debug = False
use_verbose = False
default_all = False

def debug(s):
    """print debug message
:param s: debug message string
:type s: str"""
    if use_debug:
        print(">> DEBUG: {}".format(s))

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
        cmd = hostcmd(host, *args)
        debug("runcmd: {}".format(cmd))
        output = subprocess.check_output(cmd)
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {}".format(err.returncode), file=sys.stderr)
        exit(1)
    return output

def runshell(*args):
    """run command through shell"""
    cmd = ' '.join(map(lambda x:
        x if len(x) == 1 and x in "&|><" or x == ">>" else pipes.quote(x),
        args))
    debug("runshell: {}".format(cmd))
    try:
        subprocess.check_call(cmd, shell=True)
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {}".format(err.returncode), file=sys.stderr)
        exit(1)

class Snapshot:
    """Filesystem snapshot"""
    def __init__(self, name):
        self.name = name            # snapshot name
        self.guid = None            # snapshot guid
        self.createtxg = 0          # snapshot create txn

class Streamline:
    """Streamline is a filesystem with snapshots"""
    def __init__(self, name, origin):
        self.name = name            # filesystem name
        self.origin = origin        # origin snapshot
        self.parent = None          # parent (origin) streamline
        self.snapshots = {}         # guid -> snapshot
        self.processed = False

    def first_snapshot(self):
        """get first streamline snapshot"""
        return next(self.snapshots.itervalues())

    def last_snapshot(self):
        """get last streamline snapshot"""
        return self.snapshots[next(reversed(self.snapshots.keys()))]

    def sync(self, send_streamlines, recv_streamlines, recv_parent_fs):
        if not self.snapshots:
            debug("empty snapshot list")
            return
        if self.processed:
            debug("Streamline {} is already synced, skipping".format(self.name))
            return
        #debug("self.snapshots: {}".format(self.snapshots.keys()))

        def sync_snapshot(snap, from_snap = None):
            cmd = hostcmd(send_streamlines.host, "zfs", "send", "-p", "-P")
            if use_verbose:
                cmd += ["-v"]
            if from_snap is not None:
                cmd += ["-I", from_snap.name]
            cmd += [snap.name]

            cmd += ["|"]

            cmd += hostcmd(recv_streamlines.host, "zfs", "recv", "-F", "-u")
            if use_verbose:
                cmd += ["-v"]
            if recv_parent_fs is not None:
                cmd += ["-d", recv_parent_fs]
            else:
                cmd += [snap.name.split("@")[0]]
            runshell(*cmd)

        # sync first snapshot
        first_snap = self.first_snapshot()
        if recv_streamlines.find_snapshot(first_snap) is None:
            debug("first snapshot {} (guid {}) does not exist on receiver".format(
                first_snap.name, first_snap.guid))
            if self.parent is not None:
                # sync from parent incrementally
                self.parent.sync(send_streamlines, recv_streamlines, recv_parent_fs)
                from_snap = self.parent.last_snapshot()
            else:
                # sync base version
                from_snap = None
            sync_snapshot(first_snap, from_snap)

        # sync last snapshot
        last_snap = self.last_snapshot()
        if last_snap.guid != first_snap.guid and recv_streamlines.find_snapshot(last_snap) is None:
            debug("last snapshot {} (guid {}) does not exist on receiver".format(
                last_snap.name, last_snap.guid))
            sync_snapshot(last_snap, first_snap)

        self.processed = True

class VM:
    VZ_CONF_DIR = "/etc/vz/conf"

    @staticmethod
    def list():
        vms = {}
        for vm in json.loads(runcmd(None, "vzlist", "-a", "-j")):
            # read config
            for l in open("{}/{}.conf".format(VM.VZ_CONF_DIR, vm["ctid"])):
                (name, sep, value) = l.rstrip("\n").partition("#")[0].partition("=")
                if not sep:
                    continue
                name = name.strip()
                value = value.strip().strip('"')
                if name in ("DUMPDIR"):
                    vm[name.lower()] = value
            vms[str(vm["ctid"])] = vm
        return vms

class Streamlines(dict):
    """dict of streamlines (key: name)"""
    def __init__(self, host):
        self.host = host            # streamlines host
        self.snapshots = {}         # guid -> Streamline

    def find_snapshot(self, snap):
        return self.snapshots.get(snap.guid)

    @staticmethod
    def list(host):
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
        streamlines = Streamlines(host)
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

def do_sync(cmd, args):
    try:
        opts, args = getopt.getopt(args, "n:d:")
    except getopt.GetoptError as err:
        usage(cmd, err)
    name, recv_parent_fs = None, None
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-d":
            recv_parent_fs = a
    if len(args) < 1:
        usage(cmd_pull)
    remote_host = args[0] if args[0] != "local" else None
    debug("remote_host: {}, name {}, recv_parent_fs: {}".format(remote_host, name, recv_parent_fs))

    if cmd == cmd_push:
        send_host = None
        recv_host = remote_host
    else:
        send_host = remote_host
        recv_host = None
    send_streamlines = Streamlines.list(send_host)
    recv_streamlines = Streamlines.list(recv_host)

    for s in send_streamlines.itervalues():
        if name is not None and s.name != name:
            continue
        s.sync(send_streamlines, recv_streamlines, recv_parent_fs)

def do_container_cmd(cmd, args):
    try:
        opts, args = getopt.getopt(args, "a")
    except getopt.GetoptError as err:
        usage(cmd, err)
    process_all = default_all
    for o, a in opts:
        if o == "-a":
            process_all = True
 
    vms = VM.list()
    if len(args) > 0:
        ids = Set(args)
    elif process_all:
        ids = Set(vms.iterkeys())
    else:
        usage(cmd)
    for id in ids:
        if id not in vms:
            print("Container {} does not exist".format(id), file=sys.stderr)
            continue
        cmd.do(vms[id])

###########################################################################
# commands
def cmd_list(args):
    """list command"""
    debug("list {}".format(args))
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
            l += " (origin: {})".format(s.origin)
        print(l)

        # print snapshots
        for snap in s.snapshots.itervalues():
            l = "\t{}".format(snap.name)
            if use_verbose:
                l += " (createtxg: {}, guid: {})".format(snap.createtxg, snap.guid)
            print(l)

        s.processed = True

    streamlines = Streamlines.list(None if len(args) < 1 else args[0])
    for s in sorted(streamlines.values(), key=lambda x: x.name):
        if name is not None and s.name != name:
            continue
        list_streamline(s)
cmd_list.usage = "list [-n name] [-p] [[user@]host]"
commands["list"] = cmd_list

def cmd_pull(args):
    """pull command"""
    debug("pull {}".format(args))
    do_sync(cmd_pull, args)
cmd_pull.usage = "pull [-n name] [-d local-dest-fs] [user@]host"
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {}".format(args))
    do_sync(cmd_push, args)
cmd_push.usage = "push [-n name] [-d remote-dest-fs] [user@]host"
commands["push"] = cmd_push

def do_cmd_start(vm):
    if vm["status"] == "stopped":
        runcmd(None, "vzctl", "start", str(vm["ctid"]))

def cmd_start(args):
    """start command"""
    debug("start {}".format(args))
    do_container_cmd(cmd_start, args)
cmd_start.do = do_cmd_start
cmd_start.usage = """start [-a] [ctid..]

-a  start all"""
commands["start"] = cmd_start

def do_cmd_stop(vm):
    if vm["status"] == "running":
        runcmd(None, "vzctl", "stop", str(vm["ctid"]))

def cmd_stop(args):
    """stop command"""
    debug("stop {}".format(args))
    do_container_cmd(cmd_stop, args)
cmd_stop.do = do_cmd_stop
cmd_stop.usage = """stop [-a] [ctid..]

-a  stop all"""
commands["stop"] = cmd_stop

def usage(cmd=None, error=None):
    """show usage and exit
:param cmd: command to show usage for (None - show command list)
:type cmd: command function"""
    if error is not None:
        print("Error: {}\n".format(error), file=sys.stderr)

    name = os.path.basename(sys.argv[0])
    if cmd is None:
        print("""Usage: {name} [-d] [-s] <command> [args...]

Options:
-d  debug
-v  verbose send/recv
-s  use sudo when executing remote commands

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
