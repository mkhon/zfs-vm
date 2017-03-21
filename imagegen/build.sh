#!/bin/sh

# NOTE, debootstrap and lsof required for proper built procedure

set -e

BUILDDIR=${BUILDDIR:-build}
DEBENV=${BUILDDIR}/env

. image/helpers

do_step()
{
	cmd=$1
	shift
	if [ -f ${BUILDDIR}/$cmd ]; then
		echo Skipping $cmd
		return
	fi
	echo [`date --rfc-3339=seconds`] do_step $cmd $* 
	$cmd $*
	touch ${BUILDDIR}/$cmd
}

transfer()
{
	src=$1
	dst=$2
	mkdir -p ${dst}
	tar -C ${src} -cf - . | tar -C ${dst} -xf -
}

readonly_l0_mount()
{
	env_root=$1
	src=$2
	dst=${env_root}/$3
	mkdir -p ${dst}
	mount --rbind -o ro ${src} ${dst}
	mount --rbind -o remount,ro ${src} ${dst}
}

prep_sources()
{
	build_dir=$1

	mkdir -p ${build_dir}/sources/
	transfer `pwd`/deb-packages ${build_dir}/sources/deb-packages
	transfer `pwd`/image ${build_dir}/sources/image
}

prep_env()
{
	env_root=$1
	build_dir=$2
	f_debootstrap ${env_root} --arch=amd64 --variant=minbase wheezy ${env_root} http://debian.volia.net/debian/

	mkdir -p ${env_root}/var/tmp/sources
	mount --rbind ${build_dir}/sources ${env_root}/var/tmp/sources

	# mount l0 binraies for zfs utilities if any present in l0
	readonly_l0_mount ${env_root} /sbin /usr/local/sbin
	readonly_l0_mount ${env_root} /lib /usr/local/lib
}

prep_chroot()
{
	env_root=$1
	mount --rbind /dev  ${env_root}/dev
	mount --rbind /proc ${env_root}/proc
	mount --rbind /sys  ${env_root}/sys
}

build_zfs_packages()
{
	env_root=$1
	mkdir -p ${env_root}/home/vagrant

chroot ${env_root} bash --login ${SHELLX} <<EOF
	set -e
	ln -sf /proc/mounts /etc/mtab
	cd /var/tmp/sources/deb-packages
	apt-get install -y locales
        localedef -i en_US -f UTF-8 en_US.UTF-8
	apt-get install -y patch devscripts dpkg-dev

	cd zfs-linux && sh ${SHELLX} build.sh
	cd -
	cd grub2 && sh ${SHELLX} build.sh 
	exit
EOF
}

build_image()
{
	env_root=$1
	preset=$2

# try use existing ZFS tools with fallback to fuse
if zpool list; then
echo 'Use existing ZFS utilities'
FUSE_ZFS='no'
preset=${preset:-rootds_based_debootstrapped}
else
echo 'Use ZFS fuse utilities'
FUSE_ZFS='yes'
preset=${preset:-debootstrapped}
fi

chroot ${env_root} bash --login ${SHELLX} <<EOF
	set -e
	ln -sf /proc/mounts /etc/mtab
	cd /var/tmp/sources/image
	FUSE_ZFS=${FUSE_ZFS} sh ${SHELLX} mkdeb.sh /var/tmp/zfsroot.img presets/${preset}
	exit
EOF
mv ${env_root}/var/tmp/zfsroot.img.vmdk.vagrant-vbox.box ${BUILDDIR}/
mv ${env_root}/var/tmp/zfsroot.img ${BUILDDIR}/
echo 'Disk image and vagrant box created'
du -sh ${BUILDDIR}/zfsroot.img
du -sh ${BUILDDIR}/zfsroot.img.vmdk.vagrant-vbox.box
}

cleanup_env()
{
	set +e
	env_root=$1

	zpool list 2>/dev/null| awk '/rpool/{print $1}' | xargs -I 0 -r zpool export 0

chroot ${env_root} bash --login ${SHELLX} <<EOF
	service --status-all 2>&1| awk '/\[ \+ \]/{print \$NF}' |xargs -I 0 -r service 0 stop
	sleep 5
	losetup -a | tac | awk '{print \$1}' | sed 's/://' | xargs -r -I 0 kpartx -d 0
	losetup -a | tac | awk '{print \$1}' | sed 's/://' | xargs -r -I 0 losetup -d 0
EOF

	# lsof -P | grep ${env_root} | awk '{print $2'} | sort | uniq | xargs -I 0 -r kill 0
	# wait a bit after fuse, sshd, atd killed in chroot which locks it
	sleep 5
	mount | grep ${DEBENV} | tac | awk '{print $3}' | xargs -I 0 -r umount 0
	set -e
	mount | grep ${DEBENV} | tac | awk '{print $3}' | xargs -I 0 -r /bin/false
	rm -rf ${BUILDDIR}
}

main()
{
	preset=$1
	if [ "x${FULL}" = "xyes" ]; then
		cleanup_env ${DEBENV}
	fi
	do_step prep_sources ${BUILDDIR}
	do_step prep_env ${DEBENV} ${BUILDDIR}
	do_step prep_chroot ${DEBENV}
	do_step build_zfs_packages ${DEBENV}
	do_step build_image ${DEBENV} ${preset}
}

main $*
