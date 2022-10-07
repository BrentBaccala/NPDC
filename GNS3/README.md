**Scripts to work with GNS3 as a virtual network orchestrator**

1. `gns3.py` is a library

1. `upload-image.py` uploads images to the GNS3 server

1. `ubuntu-test.py` starts a simple Ubuntu image with a cloud and a switch to provide Internet connectivity

1. Likewise, `cisco-test.py` starts a simple Cisco CSR1000v.  You must upload the Cisco image yourself.

1. `triangle-pods.py` starts a number of Cisco CSRV1000v pods, each with three routers connected in a mesh.

1. `ubuntu.py` starts a node on a GNS3 server.  It will be configured by cloud-init to
   run some startup scripts.  The main script right now is opendesktop.sh, which
   installs everything needed for a basic GNOME desktop that is automatically
   logged in as 'ubuntu' at boot.

   Main usage is:

   `./ubuntu.py  -n ubuntu -r 18 -s $((1024*1024)) --vnc --boot-script opendesktop.sh --gns3-appliance`

   `--gns3-appliance` will shutdown the node when it's finished its
   install scripts, copy the disk image to the current directory, and
   update the GNS3 appliance file in `opendesktop.gns3a`.  Expect the
   entire process to take about half an hour.

1. `add-appliance.py` adds an appliance image to the appliance file `opendesktop.gns3a`,
   which can then be imported into GNS3's GUI.
