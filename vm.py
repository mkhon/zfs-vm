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

###########################################################################
# globals
commands = collections.OrderedDict()
use_sudo = False
use_debug = False
use_verbose = False
use_noop = False
default_all = False

###########################################################################
# utility functions
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
        debug("runcmd: {}".format(" ".join(cmd)))
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

###########################################################################
# Filesystem snapshot
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

###########################################################################
# Filesystem
class Filesystem:
    """Filesystem object"""
    def __init__(self, name):
        self.name = name            # filesystem name
        self.parent = None          # parent (origin) filesystem
        self.snapshots = {}         # guid -> snapshot
        self.processed = False

    def first_snapshot(self):
        """get first filesystem snapshot"""
        return next(self.snapshots.itervalues(), None)

    def last_snapshot(self):
        """get last filesystem snapshot"""
        return self.snapshots[next(reversed(self.snapshots.keys()), None)]

    def find_snapshot(self, snapname, fuzzy=False):
        """find snapshot by name"""
        for snap in reversed(self.snapshots.values()):
            if snap.name == snapname:
                return snap
            elif fuzzy and snap.name.find(snapname) >= 0:
                return snap
        return None

    def sync(self, send_filesystems, recv_filesystems, recv_parent_fs):
        if not self.snapshots:
            debug("{}: empty snapshot list".format(self.name))
            return
        if self.processed:
            debug("Filesystem {} is already synced, skipping".format(self.name))
            return
        #debug("self.snapshots: {}".format(self.snapshots.keys()))

        def sync_snapshot(from_snap, to_snap):
            cmd = hostcmd(send_filesystems.host, "zfs", "send", "-p", "-P")
            if use_verbose:
                cmd += ["-v"]
            if use_noop:
                cmd += ["-n"]
            if from_snap:
                cmd += ["-I", from_snap.name]
            cmd += [to_snap.name]

            if not use_noop:
                cmd += ["|"]
                cmd += hostcmd(recv_filesystems.host, "zfs", "recv", "-F", "-u")
                if use_verbose:
                    cmd += ["-v"]
                if recv_parent_fs:
                    cmd += ["-d", recv_parent_fs]
                else:
                    cmd += [to_snap.name.split("@")[0]]

            runshell(None, *cmd)

        # sync first snapshot
        snapshot_iter = self.snapshots.itervalues()
        to_snap = next(snapshot_iter)
        if recv_filesystems.get_snapshot(to_snap) is None:
            debug("==> first snapshot {} (guid {}) does not exist on receiver".format(
                to_snap.name, to_snap.guid))
            if self.parent:
                # sync from parent incrementally
                self.parent.sync(send_filesystems, recv_filesystems, recv_parent_fs)
                from_snap = self.parent.last_snapshot()
            else:
                # sync base version
                from_snap = None
            sync_snapshot(from_snap, to_snap)
        else:
            debug("==> first snapshot {} (guid {}) exists on receiver".format(
                to_snap.name, to_snap.guid))
        next_from = to_snap

        # sync other snapshots
        while True:
            # find next missing snapshot (move from_snap)
            from_snap = next_from
            for snap in snapshot_iter:
                if recv_filesystems.get_snapshot(snap) is None:
                    debug("sync to: snapshot {} (guid {})".format(snap.name, snap.guid))
                    to_snap = snap
                    break
                debug("next from: snapshot {} (guid {})".format(snap.name, snap.guid))
                from_snap = snap
            else:
                # no more missing snapshots - all snapshots are synced
                break

            # find next existing snapshot (move to_snap)
            for snap in snapshot_iter:
                if recv_filesystems.get_snapshot(snap) is not None:
                    debug("sync from: snapshot {} (guid {})".format(snap.name, snap.guid))
                    next_from = snap    # next from snap
                    break
                debug("next to: snapshot {} (guid {})".format(snap.name, snap.guid))
                to_snap = snap

            # sync snapshots
            debug("snapshot {} (guid {}) does not exist on receiver".format(
                to_snap.name, to_snap.guid))
            sync_snapshot(from_snap, to_snap)

        self.processed = True

###########################################################################
# FS
class FS(dict):
    """dict of filesystems (key: name)"""
    def __init__(self, host):
        self.host = host            # host
        self.snapshots = {}         # guid -> Filesystem
        self.mountpoints = {}       # mountpoint -> Filesystem

    def get_snapshot(self, snap):
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

###########################################################################
# VM
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
        opts, args = getopt.getopt(args, "d:n:")
    except getopt.GetoptError as err:
        usage(cmd, err)
    name, recv_parent_fs = None, None
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-d":
            recv_parent_fs = a
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
        s.sync(send_filesystems, recv_filesystems, recv_parent_fs)

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

def cmd_rebase(args):
    debug("rebase {}".format(args))
    """rebase command
       Operation sequence:
       1. create new FS
       2. consolidate ALL files from all source datasets (FS's) to new FS via rsync.
          In case of multiple files with same name in different source DS's "newest" one will be kept
       3. create snapshot of consolidated dataset
       4. instantiate clones for each source DS with specified  "suffix" (.rebased is default)
       5. rsync original source DS to it's target clone, so new version became identical to original
       6. if requested remove original source datasets
       7. if requested replace original datasets with it's cloned rebased versions.
          if remove original datasets flag not set they will renamed to {original}.backup

    """
    REBASED_SUFFIX = '.rebased'
    SNAPARCHIVE_SUFFIX = '.archived'
    BACKUP_SUFFIX = '.backup'
    reverse_snapshots = True
    try:
        opts, args = getopt.getopt(args, "n:s:fdrl")
    except getopt.GetoptError as err:
        usage(cmd_list, err)
    name, rebased_suffix, force, keep_backup, replace_original = None, REBASED_SUFFIX, False, True, False
    for o, a in opts:
        if o == "-n":
            name = a
        elif o == "-s":
            rebased_suffix = a
        elif o == "-r":
            replace_original = True
        elif o == '-d':
            keep_backup = False
        elif o == "-f":
            force = True
        elif o == "-l":
            reverse_snapshots = False
    ids = sets.Set()
    if len(args) > 0:
        ids = sets.Set(args)
    if not name or not ids:
        usage(cmd_rebase)
        sys.exit(1)

    name, snap = name.split('@')
    debug("rebase: consolidated dataset:{0}, backup:{1}, datasets:{2}".format(name, rebased_suffix, ids))

    # find target DS pool to create clones...
    def rootds(ds):
        return os.path.dirname(name)
    target_rootds = rootds(name)
    def target_ds(ds):
        return os.path.join(target_rootds, os.path.basename(ds))

    def mountpoint_by_ds(ds):
        return runcmd(None, "zfs", "get", "-H", "-o", "value", "mountpoint", ds).split('\n')[0] + '/'

    def list_snapshots(ds):
        rv = runcmd(None, "zfs",  "list",  "-r",  "-t",  "snapshot",  "-H", "-o",  "name", ds).split('\n')
        rv = filter(lambda x: x, rv)
        debug("DS snapshots: {0} -> {1}".format(ds, rv))
        return rv

    def migrate_snapthos(snapshot, target_ds):
        """ TODO, rollback on failed command?! how?"""
        debug("Migrate snapshot: {0} -> {1}".format(snapshot, target_ds))
        ds, snap = snapshot.split('@')
        tmp_ds = "{0}.{1}".format(ds, snap)
        runcmd(None, "zfs", "clone", snapshot, tmp_ds)
        tmp_fs = mountpoint_by_ds(tmp_ds)
        target_fs = mountpoint_by_ds(target_ds)
        runcmd(None, "rsync", "-a", "--checksum", "--inplace", "--delete", tmp_fs, target_fs)
        runcmd(None, "zfs", "destroy", tmp_ds)
        runcmd(None, "zfs", "snapshot", "{0}@{1}".format(target_ds, snap))

    if force:
        runcmd(None, "zfs", "destroy", name) # how ignore if not exists?
    runcmd(None, "zfs", "create", name)
    # turn on ZFS block level dedup # TODO if requested?
    runcmd(None, "zfs", "set", "dedup=on", name)

    ds_destinations = dict()
    ds_mounts = dict()
    ds_snapshots = dict()
    ds_mounts.update({name: mountpoint_by_ds(name)})
    [ ds_mounts.update({ds: mountpoint_by_ds(ds)}) for ds in ids ]
    [ ds_snapshots.update({ds: list_snapshots(ds)}) for ds in ids ]
    [ ds_destinations.update({ds: target_ds(ds)}) for ds in ids ]

    rebase_ds_mount=ds_mounts[name]
    for ds in ids:
        ds_mount=ds_mounts[ds]
        runcmd(None, "rsync", "-auP", "--inplace", "--append", ds_mount, rebase_ds_mount)

    rebase_snaphot = "{0}@{1}".format(name, snap)
    runcmd(None, "zfs", "snapshot", rebase_snaphot)
    for ds in ids:
        rebased_ds = "{0}{1}".format(ds_destinations[ds], rebased_suffix)
        runcmd(None, "zfs", "clone", rebase_snaphot, rebased_ds)
        rebased_ds_mount = mountpoint_by_ds(rebased_ds)
        ds_mount=ds_mounts[ds]
        runcmd(None, "rsync", "-a", "--checksum", "--inplace", "--delete", ds_mount, rebased_ds_mount)

        # migrate snapshots to archived branch if any
        if ds_snapshots[ds]:
            snap_archive_ds = "{0}{1}".format(ds_destinations[ds], SNAPARCHIVE_SUFFIX)
            runcmd(None, "zfs", "clone", rebase_snaphot, snap_archive_ds)
            snaps_to_migrate = ds_snapshots[ds]
            if reverse_snapshots:
                snaps_to_migrate = reversed(snaps_to_migrate)
            [migrate_snapthos(snap, snap_archive_ds) for snap in snaps_to_migrate]

        if replace_original:
            # rename original DS only in case of the same destination pool/ds
            if ds == ds_destinations[ds]:
                runcmd(None, "zfs", "rename", ds, "{0}{1}".format(ds, BACKUP_SUFFIX))
            runcmd(None, "zfs", "rename", rebased_ds, ds_destinations[ds])

    if not keep_backup:
        for ds in ids:
            runcmd(None, "zfs", "destroy", "{0}{1}".format(ds, BACKUP_SUFFIX if replace_original else ""))

cmd_rebase.usage = """rebase -n name [-r] [-d] [-f] [-l] [-s suffix] [dataset...]
    -f  destroy target consolidated dataset if already present (non recursive)
    -d  remove original data sets (non recursive)
    -r  replace original datasets with re-based clones
    -n  consolidated dataset snapshot name (xyz@snap) which will be base for re-based datasets
    -s  suffix to add to original dataset name to cloned datasets
    -l  do not invert migrated snapshot streamline, default - invert"""
commands["rebase"] = cmd_rebase

###########################################################################
# list
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

###########################################################################
# push/pull
def cmd_pull(args):
    """pull command"""
    debug("pull {}".format(args))
    do_sync(cmd_pull, args)
cmd_pull.usage = """pull [-n name] [-d local-dest-fs] [user@]host
    -n  pull only snapshots with specified name
    -d  specify local destination filesystem"""
commands["pull"] = cmd_pull

def cmd_push(args):
    """push command"""
    debug("push {}".format(args))
    do_sync(cmd_push, args)
cmd_push.usage = """push [-n name] [-d remote-dest-fs] [user@]host
    -n  push only snapshots with specified name
    -d  specify remote destination filesystem"""
commands["push"] = cmd_push

###########################################################################
# checkpoint
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
    cmd = ["zfs", "snapshot", "-r", snapname]
    if use_noop:
        print(' '.join(cmd))
    else:
        runshell(False, *cmd)
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

###########################################################################
# clone
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
        cmd = ["zfs", "clone", "{}@{}".format(_fs, snap), new_fs]
        if use_noop:
            print(' '.join(cmd))
        else:
            runshell(False, *cmd)

        # rename dump if any
        if new_fs.endswith("/Dump"):
            new_mountpoint = make_new_name(_mountpoint)
            dump_filename = os.path.join(new_mountpoint, "Dump.{}".format(vm["ctid"]))
            if os.path.exists(dump_filename):
                new_dump_filename = os.path.join(new_mountpoint, "Dump.{}".format(new_ctid))
                cmd = ["mv", dump_filename, new_dump_filename]
                if use_noop:
                    print(' '.join(cmd))
                else:
                    runshell(False, *cmd)
                suspended = True

    # create new container configuration
    conf_filename = os.path.join(VM.VZ_CONF_DIR, "{}.conf".format(vm["ctid"]))
    new_conf_filename = os.path.join(VM.VZ_CONF_DIR, "{}.conf".format(new_ctid))
    cmd = ["cp", "-a", conf_filename, new_conf_filename]
    if use_noop:
        print(' '.join(cmd))
    else:
        runshell(False, *cmd)

    # start new container if old container was running
    if vm["status"] == "running":
        if suspended:
            cmd = ["vzctl", "resume", new_ctid]
        else:
            cmd = ["vzctl", "start", new_ctid]
        if use_noop:
            print(' '.join(cmd))
        else:
            runshell(False, *cmd)

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

###########################################################################
# diff
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

###########################################################################
# start
def do_start(vm, opts={}):
    if not vm["status"] == "stopped":
        return False
    cmd = ["vzctl", "start", str(vm["ctid"])]
    if use_noop:
        print(' '.join(cmd))
        return False
    else:
        return runshell(False, *cmd)

def cmd_start(args):
    """start command"""
    debug("start {}".format(args))
    do_container_cmd(cmd_start, args)
cmd_start.do = do_start
cmd_start.usage = """start [-a] [ctid...]
    -a  start all"""
commands["start"] = cmd_start

###########################################################################
# stop
def do_stop(vm, opts={}):
    if not vm["status"] == "running":
        return False
    cmd = ["vzctl", "stop", str(vm["ctid"])]
    if use_noop:
        print(' '.join(cmd))
        return False
    else:
        return runshell(False, *cmd)

def cmd_stop(args):
    """stop command"""
    debug("stop {}".format(args))
    do_container_cmd(cmd_stop, args)
cmd_stop.do = do_stop
cmd_stop.usage = """stop [-a] [ctid...]
    -a  stop all"""
commands["stop"] = cmd_stop

###########################################################################
# suspend
def do_suspend(vm, opts={}):
    if not vm["status"] == "running":
        return False
    cmd = ["vzctl", "suspend", str(vm["ctid"])]
    if use_noop:
        print(' '.join(cmd))
        return False
    else:
        return runshell(False, *cmd)

def cmd_suspend(args):
    """suspend command"""
    debug("suspend {}".format(args))
    do_container_cmd(cmd_suspend, args)
cmd_suspend.do = do_suspend
cmd_suspend.usage = """suspend [-a] [ctid...]
    -a  suspend all"""
commands["suspend"] = cmd_suspend

###########################################################################
# resume
def do_resume(vm, opts={}):
    if not vm["status"] == "stopped":
        return False
    dumpfile = "{}/Dump.{}".format(vm["dumpdir"], vm["ctid"])
    if not os.path.isfile(dumpfile):
        return False
    cmd = ["vzctl", "resume", str(vm["ctid"])]
    if use_noop:
        print(' '.join(cmd))
        return False
    else:
        return runshell(False, *cmd)

def cmd_resume(args):
    """resume command"""
    debug("resume {}".format(args))
    do_container_cmd(cmd_resume, args)
cmd_resume.do = do_resume
cmd_resume.usage = """resume [-a] [ctid...]
    -a  resume all"""
commands["resume"] = cmd_resume

###########################################################################
# usage
def usage(cmd=None, error=None):
    """show usage and exit
:param cmd: command to show usage for (None - show command list)
:type cmd: command function"""
    if error:
        print("Error: {}\n".format(error), file=sys.stderr)

    name = os.path.basename(sys.argv[0])
    if cmd is None:
        print("""Usage: {name} [-dnsv] <command> [args...]

Options:
-d  debug
-n  no-op
-s  use sudo when executing remote commands
-v  verbose

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
        opts, args = getopt.getopt(args[1:], "dhnsv")
    except getopt.GetoptError as err:
        usage(error=err)

    global use_sudo, use_debug, use_verbose, use_noop
    for o, a in opts:
        if o == "-d":
            use_debug = True
        elif o == "-h":
            usage()
        elif o == "-n":
            use_noop = True
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
