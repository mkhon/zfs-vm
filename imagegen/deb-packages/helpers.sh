need_sources=${need_sources:-y}
build_package()
{
    name=$1
    extract_hook=$2
    mkdir -p src && cd src
    if [ "x${need_sources}" = "xy" ]; then
        apt-get source --download-only ${name}
        apt-get -y build-dep ${name}
    fi
    dsc=`apt-get source --print-uris --download-only ${name} | grep .dsc | awk '{print $NF}' | tr -d /\'/`
    builddir=`dpkg-source -x $dsc | grep 'extracting' | awk '{print $NF}'`
    rm -rf ../build/${builddir}
    mv ${builddir} ../build/
    cd -
    cd build/${builddir}
    if [ -n "$extract_hook" ]; then
        $extract_hook
    fi
    debuild -d -i -us -uc -b
    cd -
}
