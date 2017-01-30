diff -ur debian/rules /home/vagrant/xxx/grub-2.02~beta3/debian/rules
--- debian/rules	2016-11-01 11:05:05.000000000 +0000
+++ /home/vagrant/xxx/grub-2.02~beta3/debian/rules	2017-01-20 20:46:34.982436426 +0000
@@ -21,15 +21,17 @@
 export HOST_CFLAGS
 export HOST_LDFLAGS
 export TARGET_CPPFLAGS := -Wno-unused-but-set-variable
-export TARGET_LDFLAGS := -no-pie
+#export TARGET_LDFLAGS := -no-pie
+export TARGET_LDFLAGS :=
+export PATH := /home/vagrant/dh_new:/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
 
-ifeq (,$(shell which qemu-system-i386 2>/dev/null))
+ifeq (,$(shell which xqemu-system-i386 2>/dev/null))
 with_check := no
 else
 with_check := yes
 endif
 
-CC := gcc-6
+CC := gcc
 
 confflags = \
 	PACKAGE_VERSION="$(deb_version)" PACKAGE_STRING="GRUB $(deb_version)" \
