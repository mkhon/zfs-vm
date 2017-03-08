#!/bin/sh

DISK=$1
rm -f box.img
ln -s $DISK ./box.img
tar --dereference -zcvf $DISK.vagrant-libvirt.box metadata.json box.img Vagrantfile
