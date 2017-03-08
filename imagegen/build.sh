#!/bin/sh

set -e

BUILDDIR=build
DEBENV=${BUILDDIR}/env

prep_sources()
{
	root_dir=$1
	build_dir=$2
	mkdir -p ${root_dir}/home/vagrant
	mkdir -p ${root_dir}/var/tmp/sources
	mkdir -p ${build_dir}/sources/
	scp -r `pwd`/deb-packages ${build_dir}/sources/
	scp -r `pwd`/image ${build_dir}/sources/
	mkdir -p ${root_dir}/var/tmp/sources
	mount --rbind ${build_dir}/sources ${root_dir}/var/tmp/sources
}

prep_env()
{
	root_dir=$1
	rm -rf ${root_dir}
	mkdir -p ${root_dir}
	debootstrap  --arch=amd64 --variant=minbase wheezy ${DEBENV} http://debian.volia.net/debian/
}

prep_chroot()
{
	root_dir=$1
	mount --rbind /dev  ${root_dir}/dev
	mount --rbind /proc ${root_dir}/proc
	mount --rbind /sys  ${root_dir}/sys
}

build_zfs_packages()
{
	root_dir=$1

chroot ${root_dir} bash --login -x <<EOF
	set -e
	ln -sf /proc/mounts /etc/mtab
	cd /var/tmp/sources/deb-packages
	apt-get install -y patch devscripts dpkg-dev

	cd zfs-linux && sh -x build.sh
	cd -
	cd grub2 && sh -x build.sh 
	exit
EOF
}

build_image()
{
	root_dir=$1
chroot ${root_dir} bash --login -x <<EOF
	set -e
	ln -sf /proc/mounts /etc/mtab
	cd /var/tmp/sources/image && sh -x mkdeb.sh /var/tmp/zfsroot.img presets/debootstrapped
	exit
EOF
}

lsof -P | grep ${DEBENV} | awk '{print $2'} | sort | uniq | xargs -I 0 -r kill 0
# wait a bit after fuse, sshd, atd killed in chroot which locks it
sleep 5
mount | grep ${DEBENV} | tac | awk '{print $3}' | xargs -I 0 -r umount 0
rm -rf ${BUILDDIR}
prep_env ${DEBENV}
prep_sources ${DEBENV} ${BUILDDIR}
prep_chroot ${DEBENV}
build_zfs_packages ${DEBENV}
build_image ${DEBENV}
