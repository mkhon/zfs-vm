zfs-vm is an utility for syncing ZFS snapshots of VM/container filesystem images.

Snapshot naming
---------------

Current version assumes that ZFS snapshot names have the following format:

@zfs-vm:streamline:version

where streamline is a VM/container filesystem image name
and version is a number that identifies the snapshot version.

Examples:

@zfs-vm:debian-7:1		- "debian-7" snapshot v1

@zfs-vm:ubuntu-12.04:28		- "ubuntu-12.04" snapshot v28

zfs-vm.py uses streamline name to identify which snapshots of different ZFS
filesystems are supposed to be the snapshot of the same VM/container image.
Version numbers are expected to be monotonically increasing.

Commands
--------

list [[user@]host]

	List zfs-vm snapshots on specified host (localhost by default)

pull [-n name] [-d local-dest-fs] [user@]host

	Pull snapshots from specified host and put them to "local-parent-fs"

	-n - pull only snapshots with the specified streamlne name
	-d - specify destination parent fs on remote

push [-n name] [-d remote-dest-fs] [user@]host

	Push snapshots to specified host and put them to "remote-parent-fs"

	-n - pull only snapshots with the specified streamlne name
	-d - specify destination parent fs on remote

tag [-n [name:]version] filesystem|container-id

	Create a snapshot of specified filesystem.
	If a filesystem was cloned from zfs-vm snapshot the streamline name
	and version are detected automatically.

Options
-------

zfs-vm.py has the following global options:

	-d	debug
	-s	use sudo on the remote side

Examples:

zfs-vm.py list

	list snapshots on localhost

zfs-vm.py -s list fjoe@remote-host

	list snapshots on remote-host

zfs-vm.py tag -n debian-7:1 pool/src/OpenVZ/Instance/1007

	create a snapshot of pool/src/OpenVZ/Instance/1007
	the name of streamline is debian-7, version 1

zfs-vm.py -s push fjoe@remote-host

	push all snapshots to remote-host

zfs-vm.py -s push -d tank/vm fjoe@remote-host

	push all snapshots to remote-host and put them to tank/vm

zfs-vm.py -s pull fjoe@remote-host

	pull all snapshots from remote-host

Push/pull
----------

If the snapshot is completely missing on the receiver side the full stream is sent,
otherwise push/pull identify the minimal incremental stream sequence required to sync
snapshots.

TODO
----

1. zfs-vm.py may identify the streamlines and the version sequence from the ZFS instead
of relying on snapshot naming (zdb, zfs send -vv can supposedly be used for that)

2. zfs-vm.py should support recursive behaviour for tagging/sending/receiving more than
one FS
