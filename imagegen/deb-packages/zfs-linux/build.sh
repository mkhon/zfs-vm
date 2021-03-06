#!/bin/sh

# jessie-backports src repo required, wheezy-backports repo required

set -e
rm -rf build
mkdir -p build

. ../helpers.sh

cp -a etc/apt/sources.list.d/sources.list /etc/apt/sources.list.d/build.list
apt-get update
patch_spl()
{
    patch -p0 < ../../spl.patch
}
build_package spl-linux patch_spl
build_package zfs-linux 
