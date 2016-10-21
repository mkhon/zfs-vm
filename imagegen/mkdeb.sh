#!/bin/bash

# For root on ZFS following instrutcion used adapated to current environment
# https://github.com/zfsonlinux/zfs/wiki/Ubuntu-16.04-Root-on-ZFS
#
# For VZ deployment following instruction used
# https://www.howtoforge.com/installing-and-using-openvz-on-debian-wheezy-amd64

set -e

debian_requirements()
{
    # debian
    codename=`lsb_release -c | awk '{print $2}'`
    if [ "x${codename}" = "xwheezy" ]; then
        apt-get install --yes kpartx debootstrap qemu-utils fakeroot alien rpm
    fi
}

detach_pool()
{
    pool=$1
    mountpoint=`zpool list -Ho altroot ${pool}`
    if [ -z "${mountpoint}" ]; then
        return
    fi
# does not work on old vagrant zfs debian image as zpool does not support -P
#    loopdev=`zpool status ${pool} -P | awk '/\/dev\/mapper/ {print $1}' | sed 's/.*\///;s/p[0-9]$//'`
    loopdev=`zpool status ${pool} | grep -A 1 ${pool}.*ONLINE | awk '/loop/ {print $1}' | sed 's/.*\///;s/p[0-9]$//'`

    umount ${mountpoint}/var/tmp/vz || /bin/true
    mount | grep -v zfs | tac | grep ${mountpoint} | awk '{print $3}' | xargs -i{} umount -lf {}
    zpool export ${pool}
    kpartx -d /dev/${loopdev}
    # ugly sleeps, sometimes loop devices does not free immediatelly?!
    sleep 1
    t1loop=`losetup /dev/${loopdev} | sed 's/.*(//;s/)//' | grep /dev/`
    losetup -d /dev/${loopdev}
    sleep 1
    if [ -n "${t1loop}" ]; then
        losetup -d ${t1loop}
    fi
}

mount_disk_image()
{
    imgfile=$1
    loop_d1=$2
    loop_d2=$3
    kpartx -d /dev/${loop_d2} || /bin/true
    losetup -d /dev/${loop_d2} || /bin/true
    losetup -d /dev/${loop_d1} || /bin/true
    losetup /dev/${loop_d1} ${imgfile} 
    losetup /dev/${loop_d2} /dev/${loop_d1}
    kpartx -a /dev/${loop_d2}
}

create_disk_image()
{
disk=$1
loop_d1=$2
loop_d2=$3
rm -f ${disk}
qemu-img create ${disk} 1G
mount_disk_image $1 $2 $3

kpartx -d /dev/${loop_d2} || /bin/true
sgdisk -o /dev/${loop_d2}
sgdisk -n9:-8M:0 -t9:BF07 /dev/${loop_d2}
sgdisk -n1:0:0 -t1:BF01 /dev/${loop_d2}
kpartx -a /dev/${loop_d2}
}

# https://github.com/zfsonlinux/zfs/wiki/Ubuntu-16.04-Root-on-ZFS
zfs_disk_markup()
{
loop_dev=$1
pool=$2
mountpoint=$3
if [ -z "${mountpoint}" ]; then
    /bin/false
fi
mkdir -p ${mountpoint}
rm -rf ${mountpoint}/*
zpool create -o ashift=12 \
      -O atime=off -O canmount=off -O compression=lz4 -O normalization=formD \
      -O mountpoint=/ -R ${mountpoint} \
      ${pool} /dev/mapper/${loop_dev}p1

zfs create -o canmount=off -o mountpoint=none ${pool}/ROOT
zfs create -o canmount=noauto -o mountpoint=/ ${pool}/ROOT/debian
zfs mount ${pool}/ROOT/debian

zfs create                 -o setuid=off              ${pool}/home
zfs create -o mountpoint=/root                        ${pool}/home/root
zfs create -o canmount=off -o setuid=off  -o exec=off ${pool}/var
zfs create -o com.sun:auto-snapshot=false             ${pool}/var/cache
zfs create                                            ${pool}/var/log
zfs create                                            ${pool}/var/spool
zfs create -o com.sun:auto-snapshot=false -o exec=on  ${pool}/var/tmp
}

install_os_base()
{
chmod 1777 ${zfs_mountpoint}/var/tmp
debootstrap --arch=amd64 --variant=minbase ${DEBIAN_VERSION} ${zfs_mountpoint} ${debootstrap_url}
}

configure_os()
{

sed -i 's/^#T0:23:respawn:/T0:23:respawn:/' ${zfs_mountpoint}/etc/inittab

mkdir -p ${zfs_mountpoint}/root/.ssh
chmod 700 ${zfs_mountpoint}/root/.ssh
#cat id_rsa.pub >> ${zfs_mountpoint}/root/.ssh/authorized_keys
echo debianvzzfs > ${zfs_mountpoint}/etc/hostname
echo "127.0.0.1 debianvzzfs" >> ${zfs_mountpoint}/etc/hosts

grep -q source.*interfaces.d ${zfs_mountpoint}/etc/network/interfaces || echo source /etc/network/interfaces.d/* >> ${zfs_mountpoint}/etc/network/interfaces
mkdir -p ${zfs_mountpoint}/etc/network/interfaces.d/
cat >${zfs_mountpoint}/etc/network/interfaces.d/eth0 <<EOF
auto eth0
iface eth0 inet dhcp
EOF

cat >${zfs_mountpoint}/etc/network/interfaces.d/eth1 <<EOF
auto eth1
iface eth1 inet dhcp
EOF

chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
#echo root:debianvzzfs | chpasswd
echo deb ${debootstrap_url} ${DEBIAN_VERSION} main > /etc/apt/sources.list
useradd -m vagrant -s /bin/bash
echo "vagrant ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vagrant
echo vagrant:vagrant | chpasswd
exit
EOF

mkdir -p ${zfs_mountpoint}/home/vagrant/.ssh
chmod 700 ${zfs_mountpoint}/home/vagrant/.ssh
cat vagrant_key.pub >> ${zfs_mountpoint}/home/vagrant/.ssh/authorized_keys
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
chown vagrant:vagrant /home/vagrant/.ssh
chown vagrant:vagrant /home/vagrant/.ssh/authorized_keys
exit
EOF
}

prep_chroot()
{
mount --rbind /dev  ${zfs_mountpoint}/dev
mount --rbind /proc ${zfs_mountpoint}/proc
mount --rbind /sys  ${zfs_mountpoint}/sys
}

install_os()
{
mkdir -p ${zfs_mountpoint}/var/tmp/cdrom
cp ${STORAGE}/${ZFSREPOPACKAGE} ${zfs_mountpoint}/var/tmp/

chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
ln -s /proc/self/mounts /etc/mtab
apt-key update
apt-get update

apt-get install --yes ${APT_OPT} lsb-release openssh-server isc-dhcp-client libcgroup1 libxml2 parted sudo
#locale-gen en_US.UTF-8
#echo 'LANG="en_US.UTF-8"' > /etc/default/locale
#dpkg-reconfigure tzdata
echo "Etc/UTC" > /etc/timezone    
dpkg-reconfigure -f noninteractive tzdata

apt-get install --yes ${APT_OPT} linux-image-amd64
# mount -o remount,ro /sys/fs/selinux
if [ -n "${EXTRA_PACKAGES}" ]; then
apt-get install --yes ${APT_OPT} ${EXTRA_PACKAGES} || /bin/true
fi
exit
EOF
}

remove_stock_kernel()
{
#w/a Abort running kernel removal
cat > ${zfs_mountpoint}/tmp/uname <<EOF
#!/bin/sh
echo 1.0
EOF
chmod +x ${zfs_mountpoint}/tmp/uname
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
PATH=/tmp:$PATH dpkg --purge linux-image-3.2.0-4-amd64 linux-image-amd64
rm -rf /boot/*.old-dkms
rm -f /tmp/uname
exit
EOF
}

finalize()
{
remove_stock_kernel
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
rm -f /etc/mtab /var/tmp/${ZFSREPOPACKAGE}
apt-get clean
echo deb http://httpredir.debian.org/debian ${DEBIAN_VERSION} main > /etc/apt/sources.list
exit
EOF

zfs snapshot ${ROOT_POOL}/ROOT/debian@install
}

prepare_vz_packages()
{
mkdir -p ${STORAGE}/vzrpms
cat vz_packages.lst | wget --no-verbose --directory-prefix=${STORAGE}/vzrpms -c -N -i -
cd ${STORAGE}/vzrpms
for f in *.rpm; do
    deb_name=`rpm -qp --qf "%{RPMTAG_NAME}_%{RPMTAG_VERSION}-%{RPMTAG_RELEASE}_amd64.deb" $f`
    if [ ! -f ${deb_name} ]; then
	    echo Converting RPM tp DEB $f
        fakeroot alien --to-deb --scripts --keep-version $f
    fi
done
cd -
}

configure_vz()
{
cat vz_sysctl.conf >> ${zfs_mountpoint}/etc/sysctl.conf
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
update-rc.d vz defaults
update-rc.d vzeventd defaults
ln -s /usr/lib64/libvzctl-4.3.1.so /usr/lib
ln -s /usr/lib64/libploop.so /usr/lib
exit
EOF
}

install_vz()
{
prepare_vz_packages
mkdir -p ${zfs_mountpoint}/var/tmp/vz
mount --rbind ${STORAGE}/vzrpms ${zfs_mountpoint}/var/tmp/vz
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
dpkg -i /var/tmp/vz/*.deb
splv=\`dpkg-query -W -f='\${Version}' spl-dkms | sed 's/-[0-9]-.*//'\`
zfsv=\`dpkg-query -W -f='\${Version}' zfs-dkms | sed 's/-[0-9]-.*//'\`

/usr/sbin/dkms build -m spl -v \${splv} -k 2.6.32-042stab117.16
/usr/sbin/dkms install -m spl -v \${splv} -k 2.6.32-042stab117.16
/usr/sbin/dkms build -m zfs -v \${zfsv} -k 2.6.32-042stab117.16
/usr/sbin/dkms install -m zfs -v \${zfsv} -k 2.6.32-042stab117.16
exit
EOF
configure_vz
}

install_zfs()
{
chroot ${zfs_mountpoint} /bin/bash --login -x <<EOF
dpkg -i /var/tmp/${ZFSREPOPACKAGE}
apt-get update
apt-get install --yes ${APT_OPT} debian-zfs
apt-get install --yes ${APT_OPT} zfs-initramfs

exit
EOF
}

install_grub()
{
mountpoint=$1
device=$2
chroot ${mountpoint} /bin/bash --login -x <<EOF
rm -f /etc/mtab
ln -s /proc/self/mounts /etc/mtab
update-initramfs -u -k all

mkdir -p /boot/grub/
cat > /boot/grub/device.map << !
(hd0)   /dev/${device}
!

DEBIAN_FRONTEND=noninteractive apt-get install --yes ${APT_OPT} grub-pc
exit
EOF

chroot ${mountpoint} /bin/bash --login -x <<EOF
grub-probe /
update-grub
sed -i 's/^#GRUB_TERMINAL=/GRUB_TERMINAL=/' /etc/default/grub
ls /boot/grub/*/zfs.mod

# w/a loopback device detection by grub...
sed -i '/loopback/d' /boot/grub/grub.cfg 
sed -i '/set root=(loop/d' /boot/grub/grub.cfg 
grub-install /dev/${device}
exit
EOF
}

build_image()
{
prepare_vz_packages
cleanup
modprobe loop || /bin/true # old kernels like VZ does not have loop module
modprobe dm-mod || /bin/true # old kernels like VZ need it for kpartx
modprobe zfs
create_disk_image ${DISK} loop0 loop1
zfs_disk_markup loop1 ${ROOT_POOL} ${zfs_mountpoint}
install_os_base
zfs set devices=off ${ROOT_POOL}

prep_chroot
install_os
install_zfs
install_vz
install_grub ${zfs_mountpoint} loop1

configure_os
finalize
}

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
    zpool import ${src_pool} ${src_pool} -N -R ${zfs_mountpoint}
    (zfs list -H -r -t filesystem -o name ${src_pool} | sort -k 4 | xargs -r -I 0 zfs mount 0) || /bin/true
    zfs destroy -r ${src_pool}@transfer || /bin/true
    zfs snapshot -r ${src_pool}@transfer

    clone_mount=/mnt/clone
    create_disk_image ${src_diskimg}.clone loop2 loop3
    zfs_disk_markup loop3 ${dst_pool} ${clone_mount}
    zpool export ${dst_pool}
    zpool import -R ${clone_mount} -N ${dst_pool}
    zfs send -R ${src_pool}@transfer | zfs recv -F ${dst_pool}
    zfs destroy -r ${dst_pool}@transfer
    detach_pool ${src_pool}

    zpool export ${dst_pool}
    rm -rf /mnt/clone/*
    zpool import -R ${clone_mount} -N ${dst_pool} ${ROOT_POOL}
    (zfs list -H -r -t filesystem -o name ${ROOT_POOL} | sort -k 4 | xargs -r -I 0 zfs mount 0) || /bin/true
    ls -lrt $clone_mount
    mount --rbind /dev  ${clone_mount}/dev
    mount --rbind /proc ${clone_mount}/proc
    mount --rbind /sys  ${clone_mount}/sys
    install_grub ${clone_mount} loop3
    detach_pool ${ROOT_POOL}    
}

convert_images()
{
detach_pool ${ROOT_POOL} || /bin/true
# !!! qcow2 non bootable at least in VBox
# !!! qemu-img convert -f raw -O qcow2 ${DISK}.clone ${DISK}.qcow2
#qemu-img convert -f raw -O vdi ${DISK}.clone ${DISK}.vdi
qemu-img convert -f raw -O vmdk ${DISK}.clone ${DISK}.vmdk && echo VMDK disk image created ${DISK}.vmdk
#qemu-img convert -f raw -O qcow ${DISK}.clone ${DISK}.qcow
# TODO vagrant box
}

make_vagrant_boxes()
{
(cd vagrant/vbox && sh makebox.sh ${DISK}.vmdk && echo Vagrant box:VirtualBox created ${DISK}.vmdk.vagrant-vbox.box )
(cd vagrant/libvirt && sh makebox.sh ${DISK}.vmdk && echo Vagrant box:Libvirt created ${DISK}.vmdk.vagrant-libvirt.box)
}

cleanup() {
set +e
detach_pool ${ROOT_POOL} >/dev/null 2>&1
# be paranoid
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

DEBIAN_VERSION=wheezy
INSTALL_VZ=${INSTALL_VZ:-yes}
ROOT_POOL=${ROOT_POOL:-rpool}
EXTRA_PACKAGES=$*
BUILDDEV_PACKAGES="qemu-utils kpartx gdisk debootstrap iptables vagrant virtualbox qemu-kvm fakeroot alien rpm"
EXTRA_PACKAGES="$EXTRA_PACKAGES man vim less"

zfs_mountpoint=/mnt/debian_loop
mkdir -p ${zfs_mountpoint}
debootstrap_url=http://debian.volia.net/debian/
#APT_OPT="--force-yes"
APT_OPT=""

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

trap cleanup EXIT INT TERM
STORAGE=~/.vzdebonzfs
mkdir -p ${STORAGE}
apt-key update
apt-get update
ZFSREPOPACKAGE=`apt-get download --print-uris zfsonlinux | awk '{print $2}'`
(cd $STORAGE && apt-get download zfsonlinux) #zfsonlinux_8_all.deb now

debian_requirements
build_image
clone_image
convert_images
make_vagrant_boxes 
