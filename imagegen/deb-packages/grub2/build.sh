#!/bin/sh

# jessie-backports src repo required, wheezy-backports repo required

set -e
(cd gcc6-simulation && sh build.sh && sudo dpkg -i gcc6-simulation_0.0.1_all.deb)
rm -f /home/vagrant/dh_new
ln -sf `pwd`/dh_new/bin /home/vagrant/dh_new

set -e
rm -rf build
mkdir -p build

. ../helpers.sh

patch_grub2()
{
    patch -p0 < ../../grub.patch
}

build_package grub2 patch_grub2
#build_package grub2
