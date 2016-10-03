zfs-vm is an utility for syncing ZFS snapshots of VM/container filesystems.

Commands
--------

* List zfs-vm snapshots on specified host (localhost by default).

		list [[user@]host]

* Pull snapshots from specified host and put them to "local-parent-fs".

		pull [-n name] [-d local-dest-fs] [user@]host
		-n - pull only snapshots with the specified streamline name
		-d - specify destination parent fs on remote

* Push snapshots to specified host and put them to "remote-parent-fs".

		push [-n name] [-d remote-dest-fs] [user@]host
		-n - pull only snapshots with the specified streamline name
		-d - specify destination parent fs on remote

* Rebase collection of datasets by creating consolidated dataset and creating clone for each source dataset based on this consolidated dataset.

		rebase -n name [-r] [-d] [-f] [-s suffix] [dataset...]
		-n - consolidated dataset snapshot name (xyz@snap) which be base for re-based datasets
		-f - destroy target consolidated dataset if already present (non recursive)
		-d - remove original data sets (non recursive)
		-r - replace original datasets with re-based clones
		-s - suffix to add to original dataset name to cloned datasets

Options
-------

zfs-vm.py has the following global options:

	-d	debug
	-n	no-op
	-s	use sudo on the remote side
	-v	verbose

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

* Rebase dataset (file systems) collection by creating consolidated data set and replcing original datasets by it's clones instaltiated from consolidated data set and keeping backup of original ones.

		zfs-vm.py -s rebase -n tank/vm/merged@today -r tank/vm/vm1 tank/vm/vm2 tank/vm/vm10

Push/pull
----------

If the snapshot is completely missing on the receiver side the full stream is sent,
otherwise push/pull identify the minimal incremental stream sequence required to sync
snapshots.

TODO
----

1. zfs-vm.py should support recursive behaviour for pushing/pulling more than one FS.
