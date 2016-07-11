zfs-vm is an utility for syncing ZFS snapshots of VM/container filesystems.

Commands
--------

* List zfs-vm snapshots on specified host (localhost by default).

		list [[user@]host]

* Pull snapshots from specified host and put them to "local-parent-fs".

		pull [-p] [-n name] [-d local-dest-fs] [user@]host
		-p - print sync commands only
		-n - pull only snapshots with the specified streamlne name
		-d - specify destination parent fs on remote

* Push snapshots to specified host and put them to "remote-parent-fs".

		push [-p] [-n name] [-d remote-dest-fs] [user@]host
		-p - print sync commands only
		-n - pull only snapshots with the specified streamlne name
		-d - specify destination parent fs on remote

Options
-------

zfs-vm.py has the following global options:

	-d	debug
	-s	use sudo on the remote side

Examples:

* List snapshots on localhost.

		zfs-vm.py list

* List snapshots on remote-host.

		zfs-vm.py -s list fjoe@remote-host

* Push all snapshots to remote-host.

		zfs-vm.py -s push fjoe@remote-host

* Push all snapshots to remote-host and put them to tank/vm.

		zfs-vm.py -s push -d tank/vm fjoe@remote-host

* Pull all snapshots from remote-host.

		zfs-vm.py -s pull fjoe@remote-host

Push/pull
----------

If the snapshot is completely missing on the receiver side the full stream is sent,
otherwise push/pull identify the minimal incremental stream sequence required to sync
snapshots.

TODO
----

1. zfs-vm.py should support recursive behaviour for pushing/pulling more than one FS.
