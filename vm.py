#!/usr/bin/env python
from __future__ import print_function
import os, sys
import getopt
import subprocess
import pipes
import collections
import json
import sets
import time

# globals
commands = collections.OrderedDict()
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
    if host:
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
        return subprocess.check_output(cmd)
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {}".format(err.returncode), file=sys.stderr)
        exit(1)

def runshell(return_output, *args):
    """run command through shell"""
    cmd = ' '.join(map(lambda x:
        x if len(x) == 1 and x in "&|><" or x == ">>" else pipes.quote(x),
        hostcmd(None, *args) if return_output is not None else args))
    debug("runshell: {}".format(cmd))
    try:
        if return_output:
            return subprocess.check_output(cmd, shell=True)

        subprocess.check_call(cmd, shell=True)
        return True
    except subprocess.CalledProcessError as err:
        print("Command returned exit code {}".format(err.returncode), file=sys.stderr)
        exit(1)

class Snapshot:
    """Filesystem snapshot"""
    def __init__(self, name):
        self.name = name            # snapshot name
        self.guid = None            # snapshot guid
        self.createtxg = 0          # snapshot create txn

    def num_changes(self):
        fsname = self.name.split("@")[0]
        output = runshell(True, "zfs", "diff", self.name, fsname, "|", "wc", "-l").rstrip("\n")
        debug("snapshot {}: {} changes".format(self.name, output))
        return int(output)

class Filesystem:
    """Filesystem object"""
    def __init__(self, name):
        self.name = name            # filesystem name
        self.parent = None          # parent (origin) filesystem
        self.snapshots = {}         # guid -> snapshot
        self.processed = False

    def first_snapshot(self):
        """get first filesystem snapshot"""
        if len(self.snapshots) == 0:
            return None
        return next(self.snapshots.itervalues())

    def last_snapshot(self):
        """get last filesystem snapshot"""
        if len(self.snapshots) == 0:
            return None
        return self.snapshots[next(reversed(self.snapshots.keys()))]

    def find_snapshot(self, snapname, fuzzy=False):
        """find snapshot by name"""
        for snap in reversed(self.snapshots.values()):
            if snap.name == snapname:
                return snap
            elif snap.name.find(snapname) >= 0:
                return snap
        return None

    def sync(self, send_filesystems, recv_filesystems, recv_parent_fs, print_only):
        if not self.snapshots:
            debug("empty snapshot list")
            return
        if self.processed:
            debug("Filesystem {} is already synced, skipping".format(self.name))
            return
        #debug("self.snapshots: {}".format(self.snapshots.keys()))

        def sync_snapshot(snap, from_snap = None):
            cmd = hostcmd(send_filesystems.host, "zfs", "send", "-p", "-P")
            if use_verbose:
                cmd += ["-v"]
            if from_snap:
                cmd += ["-I", from_snap.name]
            cmd += [snap.name]

            cmd += ["|"]

            cmd += hostcmd(recv_filesystems.host, "zfs", "recv", "-F", "-u")
            if use_verbose:
                cmd += ["-v"]
            if recv_parent_fs:
                cmd += ["-d", recv_parent_fs]
            else:
                cmd += [snap.name.split("@")[0]]
            if print_only:
                print(' '.join(cmd))
            else:
                runshell(None, *cmd)

        # sync first snapshot
        first_snap = self.first_snapshot()
        if recv_filesystems.find_snapshot(first_snap) is None:
            debug("first snapshot {} (guid {}) does not exist on receiver".format(
                first_snap.name, first_snap.guid))
            if self.parent:
                # sync from parent incrementally
                self.parent.sync(send_filesystems, recv_filesystems, recv_parent_fs, print_only)
                from_snap = self.parent.last_snapshot()
            else:
                # sync base version
                from_snap = None
            sync_snapshot(first_snap, from_snap)

        # sync last snapshot
        last_snap = self.last_snapshot()
        if last_snap.guid != first_snap.guid and recv_filesystems.find_snapshot(last_snap) is None:
            debug("last snapshot {} (guid {}) does not exist on receiver".format(
                last_snap.name, last_snap.guid))
            sync_snapshot(last_snap, first_snap)

        self.processed = True

class FS(dict):
    """dict of filesystems (key: name)"""
    def __init__(self, host):
        self.host = host            # host
        self.snapshots = {}         # guid -> Filesystem
        self.mountpoints = {}       # mountpoint -> Filesystem

    def find_snapshot(self, snap):
        return self.snapshots.get(snap.guid)

    @staticmethod
    def list(host):
        """list filesystems on host
:param host: host to list filesystems on (localhost if None)
:type host: str
:returns: filesystems on specified host
:rtype: dict of Filesystems (by name)"""
        # get filesystem origins
        filesystems = FS(host)
        for l in runcmd(host, "zfs", "get", "-H", "-p", "-o", "name,property,value", "-t", "filesystem", "origin,mountpoint").split("\n"):
            # pool/vm/Root3   origin  pool/vm/Root2@zfs-vm:foo:6
            if not l:
                continue
            #debug(l)
            (fsname, propname, value) = l.split("\t")
            if value == "-":
                value = None
            if propname == "origin":
                filesystems[fsname] = Filesystem(fsname)
            setattr(filesystems[fsname], propname, value)

        # get filesystem snapshots
        for l in runcmd(host, "zfs", "get", "-H", "-p", "-o", "name,property,value", "-t", "snapshot", "guid,createtxg").split("\n"):
            # pool/src/OpenVZ@pool-src-OpenVZ-20150529-Initial  createtxg   1379    -
            if not l:
                continue
            #debug(l)
            (snapname, propname, value) = l.split("\t")
            if value == "-":
                continue    # empty value

            fsname = snapname.split("@")[0]
            fs = filesystems[fsname]
            if propname == "guid":
                guid = value
                fs.snapshots[guid] = Snapshot(snapname)
            setattr(fs.snapshots[guid], propname, value)

        # build parent relation, snapshots and mountpoints dicts
        for fs in filesystems.itervalues():
            if fs.origin:
                fs.parent = filesystems.get(fs.origin.split("@")[0])
            for snap in fs.snapshots.itervalues():
                filesystems.snapshots[snap.guid] = fs
            if fs.mountpoint:
                filesystems.mountpoints[fs.mountpoint] = fs
            # sort Filesystem snapshots by "createtxg"
            fs.snapshots = collections.OrderedDict(
                sorted(fs.snapshots.items(), key=lambda x: int(x[1].createtxg)))

        return filesystems

class VM(dict):
    """dict of filesystems (key: name)"""

    VZ_CONF_DIR = "/etc/vz/conf"

    def __init__(self):
        self.names = {}   # name -> container

    @staticmethod
    def list():
        filesystems = FS.list(None)

        # get all VMs
        vms = VM()
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
            privatefs = vm["private"]
            if privatefs in filesystems.mountpoints:
                vm["privatefs"] = filesystems.mountpoints[privatefs]
            parentfs = os.path.dirname(privatefs)
            if parentfs in filesystems.mountpoints:
                vm["parentfs"] = filesystems.mountpoints[parentfs]
            vms[str(vm["ctid"])] = vm

        # build name dict
        for vm in vms.itervalues():
            vms.names[vm["name"]] = vm

        return vms

def do_sync(cmd, args):
    try:
        opts, args = getopt.getopt(args, "d:n:p")
    except getopt.GetoptError as err:
        usage(cmd, err)
    name, recv_parent_fs, print_only = None, None, False
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-d":
            recv_parent_fs = a
        elif o == "-p":
            print_only = True
    if len(args) < 1:
        usage(cmd)
    remote_host = args[0] if args[0] != "local" else None
    debug("remote_host: {}, name {}, recv_parent_fs: {}".format(remote_host, name, recv_parent_fs))

    if cmd == cmd_push:
        send_host = None
        recv_host = remote_host
    else:
        send_host = remote_host
        recv_host = None
    send_filesystems = FS.list(send_host)
    recv_filesystems = FS.list(recv_host)

    for s in send_filesystems.itervalues():
        if name and s.name != name:
            continue
        s.sync(send_filesystems, recv_filesystems, recv_parent_fs, print_only)

def do_container_cmd(cmd, args, options="", allow_all=True):
    try:
        if not default_all and allow_all:
            options += "a"
        opts, args = getopt.getopt(args, options)
    except getopt.GetoptError as err:
        usage(cmd, err)
    process_all = default_all and allow_all
    other_opts = {}
    for o, a in opts:
        if o == "-a":
            process_all = True
        else:
            other_opts[o] = a
 
    vms = VM.list()
    if len(args) > 0:
        ids = sets.Set(args)
    elif process_all:
        ids = sets.Set(vms.iterkeys())
    else:
        usage(cmd)
    for id in ids:
        if id not in vms:
            if id not in vms.names:
                print("Container {} does not exist".format(id), file=sys.stderr)
                continue
            id = str(vms.names[id]["ctid"])
        cmd.do(vms[id], other_opts)

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

    def list_filesystem(s):
        if s.processed:
            return
        if s.parent and list_parents:
            list_filesystem(s.parent)

        # print filesystem
        l = s.name
        if s.origin:
            l += " (origin: {})".format(s.origin)
        print(l)

        # print snapshots
        for snap in s.snapshots.itervalues():
            l = "\t{}".format(snap.name)
            if use_verbose:
                l += " (createtxg: {}, guid: {})".format(snap.createtxg, snap.guid)
            print(l)

        s.processed = True

    filesystems = FS.list(None if len(args) < 1 else args[0])
    for s in sorted(filesystems.values(), key=lambda x: x.name):
        if name and s.name != name:
            continue
        list_filesystem(s)
cmd_list.usage = """list [-n name] [-p] [[user@]host]
    -n  list only snapshots with specified name
    -p  include parents"""
commands["list"] = cmd_list

def cmd_pull(args):
    """pull command"""
    debug("pull {}".format(args))
    do_sync(cmd_pull, args)
cmd_pull.usage = """pull [-n name] [-d local-dest-fs] [user@]host
    -p  print sync commands only
    -n  pull only snapshots with specified name
    -d  specify local destination filesystem"""
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {}".format(args))
    do_sync(cmd_push, args)
cmd_push.usage = """push [-n name] [-d remote-dest-fs] [user@]host
    -p  print sync commands only
    -n  push only snapshots with specified name
    -d  specify remote destination filesystem"""
commands["push"] = cmd_push

def do_snapshot(vm, description):
    privatefs = vm.get("privatefs")
    if privatefs is None:
        return None

    # do snapshot of parent fs (if any) or private fs
    parentfs = vm.get("parentfs")
    if parentfs:
        snapfs = parentfs
    else:
        snapfs = privatefs

    # check if nothing to do
    privatefs_lastsnap = privatefs.last_snapshot()
    snapfs_lastsnap = snapfs.last_snapshot()
    if privatefs_lastsnap and snapfs_lastsnap:
        if description is None and privatefs_lastsnap.num_changes() == 0:
            debug("Empty description and no changes - skipping snapshot")
            return snapfs_lastsnap.name

    def make_snapname(ts):
        snapname = snapfs.name.replace("/", "-") + "-" + ts
        if description:
            snapname += "-" + description
        return snapfs.name + "@" + snapname

    t = time.localtime()
    snapname = make_snapname(time.strftime("%Y%m%d", t))
    if snapfs.find_snapshot(snapname):
        snapname = make_snapname(time.strftime("%Y%m%d%H%M", t))
        if snapfs.find_snapshot(snapname):
            print("Snapshot {} already exists".format(snapname), file=sys.stderr)
            sys.exit(1)
    runshell(False, "zfs", "snapshot", "-r", snapname)
    return snapname

def do_checkpoint(vm, opts):
    # stop/suspend container if running
    full_stop = opts.get("-S") is not None
    if full_stop:
        suspended = do_stop(vm)
    else:
        suspended = do_suspend(vm)

    # create snapshot
    snapname = do_snapshot(vm, opts.get("-d"))

    # resume container if suspended
    if suspended:
        vm["status"] = "stopped"
        if not do_resume(vm):
            do_start(vm)

    # return new snapshot name
    return snapname

def cmd_checkpoint(args):
    """checkpoint command"""
    debug("checkpoint {}".format(args))
    do_container_cmd(cmd_checkpoint, args, "d:S")
cmd_checkpoint.do = do_checkpoint
cmd_checkpoint.usage = """checkpoint [-a] [-S] [-d description] [ctid...]
    -a  checkpoint all
    -S  fully stop the container before making snapshot
    -d  specify snapshot description"""
commands["checkpoint"] = cmd_checkpoint

def do_clone(vm, opts={}):
    # determine source snapshot
    if "-s" in opts:
        # operate on specified snapshot
        privatefs = vm.get("privatefs")
        if privatefs is None:
            return None
        parentfs = vm.get("parentfs")
        if parentfs is None:
            snapfs = privatefs
        else:
            snapfs = parentfs

        snapname = opts["-s"]
        snap = snapfs.find_snapshot(snapname, fuzzy=True)
        if snap is None:
            print("Snapshot {} of filesystem {} not found".format(snapname, snapfs.name), file=sys.stderr)
            sys.exit(1)
        snapname = snap.name
    else:
        # create a new snapshot
        snapname = do_checkpoint(vm, opts)

    # determine ctid
    vms = VM.list()
    if "-i" in opts:
        new_ctid = opts["-i"]
    else:
        new_ctid = str(reduce(lambda x, y: max(x, int(y)), vms.iterkeys(), 0) + 1)
    if new_ctid in vms:
        print("Container {} already exists".format(new_ctid))
        return None

    def make_new_name(old_name):
        return (old_name+"/").replace("/{}/".format(vm["ctid"]), "/{}/".format(new_ctid)).rstrip("/")

    debug("clone: new ctid {}, source snapshot {}".format(new_ctid, snapname))

    (fs, snap) = snapname.split("@")
    # clone filesystems (recursively)
    suspended = False
    for l in runcmd(None, "zfs", "list", "-H", "-r", "-o", "name,mountpoint", fs).split("\n"):
        if not l:
            continue
        (_fs, _mountpoint) = l.split("\t")
        new_fs = make_new_name(_fs)
        runshell(False, "zfs", "clone", "{}@{}".format(_fs, snap), new_fs)

        # rename dump if any
        if new_fs.endswith("/Dump"):
            new_mountpoint = make_new_name(_mountpoint)
            dump_filename = os.path.join(new_mountpoint, "Dump.{}".format(vm["ctid"]))
            if os.path.exists(dump_filename):
                new_dump_filename = os.path.join(new_mountpoint, "Dump.{}".format(new_ctid))
                runshell(False, "mv", dump_filename, new_dump_filename)
                suspended = True

    # create new container configuration
    conf_filename = os.path.join(VM.VZ_CONF_DIR, "{}.conf".format(vm["ctid"]))
    new_conf_filename = os.path.join(VM.VZ_CONF_DIR, "{}.conf".format(new_ctid))
    runshell(False, "cp", "-a", conf_filename, new_conf_filename)

    # start new container if old container was running
    if vm["status"] == "running":
        if suspended:
            runshell(False, "vzctl", "resume", new_ctid)
        else:
            runshell(False, "vzctl", "start", new_ctid)

def cmd_clone(args):
    """clone command"""
    debug("clone {}".format(args))
    do_container_cmd(cmd_clone, args, "d:Si:n:s:", allow_all=False)
cmd_clone.do = do_clone
cmd_clone.usage = """clone [-s snapshot] [-i id] [-n name] [-S] [-d description] ctid
    -s  source container snapshot (default: clone from live container)
    -i  new container id (default: allocate next unused ctid)
    -n  new container name
    -S  fully stop the container before making snapshot
    -d  new snapshot description"""
commands["clone"] = cmd_clone

def do_diff(vm, opts={}):
    fs = vm.get("privatefs")
    if fs is None:
        return False
    snapname = opts.get("-s")
    if snapname:
        snapfrom = fs.find_snapshot(snapname, fuzzy=True)
        if snapfrom is None:
            print("No snapshots like {} found for {}".format(snapname, fs.mountpoint), file=sys.stderr) 
            sys.exit(1)
    else:
        snapfrom = fs.last_snapshot()
        if snapfrom is None:
            print("{} does not have snapshots".format(fs.mountpoint), file=sys.stderr)
            sys.exit(1)
    snapname = opts.get("-S")
    snapto = None
    if snapname:
        snapto = fs.find_snapshot(snapname, fuzzy=True)
        if snapto is None:
            print("No snapshots like {} found for {}".format(snapname, fs.mountpoint), file=sys.stderr) 
            sys.exit(1)
    return runshell(False, "zfs", "diff", "-F", "-t", snapfrom.name, fs.name if snapto is None else snapto.name)

def cmd_diff(args):
    """diff command"""
    debug("diff {}".format(args))
    do_container_cmd(cmd_diff, args, "s:S:")
cmd_diff.do = do_diff
cmd_diff.usage = """diff [-s snapname] [-S snapname] [ctid...]
    -a  diff all
    -s  diff from snapshot (default: last snapshot)
    -S  diff to snapshot (default: live filesystem)"""
commands["diff"] = cmd_diff

def do_start(vm, opts={}):
    if not vm["status"] == "stopped":
        return False
    return runshell(False, "vzctl", "start", str(vm["ctid"]))

def cmd_start(args):
    """start command"""
    debug("start {}".format(args))
    do_container_cmd(cmd_start, args)
cmd_start.do = do_start
cmd_start.usage = """start [-a] [ctid...]
    -a  start all"""
commands["start"] = cmd_start

def do_stop(vm, opts={}):
    if not vm["status"] == "running":
        return False
    return runshell(False, "vzctl", "stop", str(vm["ctid"]))

def cmd_stop(args):
    """stop command"""
    debug("stop {}".format(args))
    do_container_cmd(cmd_stop, args)
cmd_stop.do = do_stop
cmd_stop.usage = """stop [-a] [ctid...]
    -a  stop all"""
commands["stop"] = cmd_stop

def do_suspend(vm, opts={}):
    if not vm["status"] == "running":
        return False
    return runshell(False, "vzctl", "suspend", str(vm["ctid"]))

def cmd_suspend(args):
    """suspend command"""
    debug("suspend {}".format(args))
    do_container_cmd(cmd_suspend, args)
cmd_suspend.do = do_suspend
cmd_suspend.usage = """suspend [-a] [ctid...]
    -a  suspend all"""
commands["suspend"] = cmd_suspend

def do_resume(vm, opts={}):
    if not vm["status"] == "stopped":
        return False
    dumpfile = "{}/Dump.{}".format(vm["dumpdir"], vm["ctid"])
    if not os.path.isfile(dumpfile):
        return False
    return runshell(False, "vzctl", "resume", str(vm["ctid"]))

def cmd_resume(args):
    """resume command"""
    debug("resume {}".format(args))
    do_container_cmd(cmd_resume, args)
cmd_resume.do = do_resume
cmd_resume.usage = """resume [-a] [ctid...]
    -a  resume all"""
commands["resume"] = cmd_resume

def usage(cmd=None, error=None):
    """show usage and exit
:param cmd: command to show usage for (None - show command list)
:type cmd: command function"""
    if error:
        print("Error: {}\n".format(error), file=sys.stderr)

    name = os.path.basename(sys.argv[0])
    if cmd is None:
        print("""Usage: {name} [-d] [-s] <command> [args...]

Options:
-d  debug
-v  verbose send/recv
-s  use sudo when executing remote commands

Commands:""".format(name=name), file=sys.stderr)
        for c in commands:
            print("{usage}".format(name=name, usage=commands[c].usage))
    else:
        print("Usage: {name} {usage}".format(name=name, usage=cmd.usage))
    sys.exit(1)

###########################################################################
# main function
def main(args):
    """main function"""
    global default_all
    if os.getenv("VM_DEFAULT_ALL"):
        default_all = True

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
