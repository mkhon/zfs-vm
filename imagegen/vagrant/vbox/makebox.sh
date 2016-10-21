#!/bin/sh

DISK=$1
rm -f box-disk1.vmdk
ln -s $DISK ./box-disk1.vmdk
tar --dereference -zcvf $DISK.vagrant-vbox.box box.ovf box-disk1.vmdk Vagrantfile
