#!/usr/bin/env python
from __future__ import print_function
import collections

vm = __import__("zfs-vm")
s = vm.Streamline("foo")

v1 = {
    1: 'fs1',
    2: 'fs1',
    3: 'fs1',
    4: 'fs2',   # single version
    5: 'fs3',   # two versions
    6: 'fs3',   
    7: 'fs4',   # three versions
    8: 'fs4',
    9: 'fs4',
    10: 'fs5',   # single version
}
s.versions = collections.OrderedDict(sorted(v1.items(), key=lambda x: int(x[0])))

# start from the middle: 3 4 5 6 7 9 10
print("t0: {0}".format(' '.join([str(v) for v in vm.VersionIterator(s, 2)])))
# start from the end: empty
print("t1: {0}".format(' '.join([str(v) for v in vm.VersionIterator(s, 10)])))
# start from non-existing item: empty
print("t2: {0}".format(' '.join([str(v) for v in vm.VersionIterator(s, 11)])))

v2 = {
    1: 'fs1',
    2: 'fs1',
    3: 'fs2',   # two versions
    4: 'fs2',
}
s.versions = collections.OrderedDict(sorted(v2.items(), key=lambda x: int(x[0])))

# start from fs change point, two versions at the end: 3 4
print("t3: {0}".format(' '.join([str(v) for v in vm.VersionIterator(s, 2)])))

# vi: ts=4:sw=4:et:
