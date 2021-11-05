# NPDC

Network Programmability scripts for Data Centers

We use several platforms:

1. Amazon Web Services (AWS)
1. Cisco Unified Computing System (UCS)
1. GNS3
1. Cisco Virtual Routers and Switches

# GNS3 Ubuntu Desktop images

One of the nicest pieces of this project is a script that uses a GNS3 server to build GNS3 appliances that look like this
on the GNS3 GUI when you boot them for the first time:

![screenshot of Ubuntu Bionic desktop](https://github.com/BrentBaccala/NPDC/blob/master/ubuntu-bionic.png?raw=true)

It's similar to the images you can get from osboxes.org, but we provide just the build script
to build from the Canonical-distributed Ubuntu images, so you can build your own appliances,
and tweak the one we've got.

It's in the GNS3/ directory.

It's not quite working right.  See the Issues tabs.
