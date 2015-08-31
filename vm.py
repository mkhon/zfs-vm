#!/usr/bin/env python
from __future__ import print_function
import sys
import os
import getopt
import subprocess

# constants
VM_PROPERTY_STREAMLINE = "vm:streamline"

# globals
use_sudo = False
use_debug = False

def debug(s):
    if use_debug:
        print(">> DEBUG: {0}".format(s))

def usage():
    name = os.path.basename(sys.argv[0])
    print("""Usage: {name} [-s] <command> [args...]

Options:
-s	use sudo when executing remote commands

Commands:
list [[<user>@]<host>]
tag [<name>:]<version> <filesystem|container-id>
push <name> [[<user>@]<host>]:pool
pull <name> [[<user>@]<host>]:pool""".format(name=name), file=sys.stderr)
    sys.exit(1)

def runcmd(host, *args):
    cmd = []
    if host is not None:
        cmd += ["ssh", host]
    if use_sudo:
        cmd += ["sudo"]
    cmd += args
    debug("{0}: {1}".format(runcmd.__name__, ' '.join(cmd)))
    try:
        output = subprocess.check_output(cmd)
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

def do_list(args):
    debug("list {0}".format(args))
    for name, s in sorted(Streamline.get(None if len(args) < 1 else args[0]).iteritems()):
        for v in sorted(s.versions, key=int):
            print("{name}:{version}".format(name=name, version=v))

def do_tag(args):
    debug("tag {0}".format(args))
    pass

def do_push(args):
    debug("push {0}".format(args))
    pass

def do_pull(args):
    debug("pull {0}".format(args))
    pass

commands = {
    "list": do_list,
    "tag": do_tag,
    "push": do_push,
    "pull": do_pull,
}

def main(args):
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
