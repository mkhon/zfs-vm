#! /bin/bash
dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz

# deb file:/usr/local/mydebs ./
echo deb [trusted=yes] file:`pwd` ./
