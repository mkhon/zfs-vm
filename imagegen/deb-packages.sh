set -e
prep_chroot()
{
    root_dir=$1
    mount --rbind /dev  ${root_dir}/dev
    mount --rbind /proc ${root_dir}/proc
    mount --rbind /sys  ${root_dir}/sys
    mkdir -p ${root_dir}/${CHROOT_STORAGE}
}

BUILDDIR=build
DEBENV=${BUILDDIR}/env

mount | grep ${DEBENV} | tac | awk '{print $3}' | xargs -I 0 -r umount 0

rm -rf ${BUILDDIR}
mkdir -p ${DEBENV}
debootstrap  --arch=amd64 --variant=minbase wheezy ${DEBENV} http://debian.volia.net/debian/

mkdir -p ${DEBENV}/var/tmp/${BUILDDIR}
mkdir -p ${DEBENV}/home/vagrant
mkdir -p ${DEBENV}/var/tmp/deb-packages
scp -r `pwd`/deb-packages ${BUILDDIR}/
mount --rbind ${BUILDDIR}/deb-packages ${DEBENV}/var/tmp/deb-packages

chroot ${DEBENV} bash --login -x <<EOF
set -e
cd /var/tmp/deb-packages
apt-get install -y patch devscripts dpkg-dev

cd zfs-linux && sh -x ${BUILDDIR}.sh
cd -
cd grub2 && sh -x ${BUILDDIR}.sh 
EOF
