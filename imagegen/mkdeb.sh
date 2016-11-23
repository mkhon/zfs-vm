#!/bin/bash

# For root on ZFS following instrutcion used adapated to current environment
# https://github.com/zfsonlinux/zfs/wiki/Ubuntu-16.04-Root-on-ZFS
#
# For VZ deployment following instruction used
# https://www.howtoforge.com/installing-and-using-openvz-on-debian-wheezy-amd64

set -e

clone_image()
{
    src_pool=${ROOT_POOL}
    src_diskimg=${DISK}
    dst_pool=d${ROOT_POOL}
    detach_pool ${src_pool} || /bin/true
    detach_pool ${dst_pool} || /bin/true
    # remount with other name
    zpool export ${dst_pool} || /bin/true
    zpool export ${src_pool} || /bin/true
    mount_disk_image ${src_diskimg} loop0 loop1
    zpool import ${src_pool} -N -R ${ZFS_MOUNTPOINT}
    pool_mount ${src_pool}
#    (zfs list -H -r -t filesystem -o name ${src_pool} | sort -k 4 | xargs -r -I 0 zfs mount 0) || /bin/true
    zfs destroy -r ${src_pool}@transfer || /bin/true
    zfs snapshot -r ${src_pool}@transfer

    clone_mount=/mnt/clone
    create_disk_image ${src_diskimg}.clone loop2 loop3 1G
    zfs_pool loop3 ${dst_pool} ${clone_mount}

    zpool export ${dst_pool} || /bin/true
    zpool import -R ${clone_mount} -N ${dst_pool}
    zfs destroy ${dst_pool}/ROOT/debian
    zfs send -R ${src_pool}@transfer | zfs recv -F ${dst_pool}
    zfs destroy -r ${dst_pool}@transfer
    detach_pool ${src_pool}
    zpool export ${dst_pool}
    rm -rf /mnt/clone/*
    zpool import -R ${clone_mount} -N ${dst_pool} ${ROOT_POOL}
    pool_mount ${ROOT_POOL}
#    (zfs list -H -r -t filesystem -o name ${ROOT_POOL} | sort -k 4 | xargs -r -I 0 zfs mount 0) || /bin/true
    ls -lrt $clone_mount
    mount --rbind /dev  ${clone_mount}/dev
    mount --rbind /proc ${clone_mount}/proc
    mount --rbind /sys  ${clone_mount}/sys
    install_grub ${clone_mount} loop3
    zfs list -t all ${ROOT_POOL}
    detach_pool ${ROOT_POOL}    
}

convert_image()
{
# !!! qcow2 non bootable at least in VBox, but bootable in KVM (libvirt)
    img=$1
    fmt=$2
    qemu-img convert -f raw -O ${fmt} ${img}.clone ${img}.${fmt} && echo -n ${fmt} disk image created ${img}.${fmt} && du -sh ${img}.${fmt}
}

convert_images()
{
    disk=$1
    shift
    to_fmt=$*
    for f in ${to_fmt}; do
        convert_image $disk $f
    done
}

make_vagrant_boxes()
{
(cd vagrant/vbox && sh makebox.sh ${DISK}.vmdk && echo -n Vagrant box:VirtualBox created ${DISK}.vmdk.vagrant-vbox.box && du -sh ${DISK}.vmdk.vagrant-vbox.box)
#(cd vagrant/libvirt && sh makebox.sh ${DISK}.qcow2 && echo Vagrant box:Libvirt created ${DISK}.vmdk.vagrant-libvirt.box)
}

cleanup() {
set +e
detach_pool ${ROOT_POOL} >/dev/null 2>&1
# be paranoid
return
bash >/dev/null 2>&1 <<EOF
kpartx -d /dev/loop3
losetup -d /dev/loop3
losetup -d /dev/loop2

kpartx -d /dev/loop1
losetup -d /dev/loop1
losetup -d /dev/loop0
EOF
set -e
}
#trap cleanup EXIT INT TERM

usage()
{
    echo "Syntax: $0 <disk_image_filename>"
    exit 1
}
DISK=$1
if [ -z "$DISK" ]; then
    usage
fi
shift
if [ -n "$1" ]; then
    preset=$1
    shift
fi
if [ -z "${preset}" ]; then
    preset=presets/rootds_based_debootstrapped
fi
. ${preset}
EXTRA_PACKAGES=$*

. `dirname $0`/buildimage-functions

debian_requirements
build_image ${EXTRA_PACKAGES}
clone_image
convert_images ${DISK} vmdk #qcow2 vdi qcow...
make_vagrant_boxes 

# vi: set filetype=sh expandtab sw=4 ts=4 :
