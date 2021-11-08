
`ubuntu.py` starts a node on a GNS3 server.  It will be configured by cloud-init to
run some startup scripts.  The main script right now is opendesktop.sh, which
installs everything needed for a basic GNOME desktop that is automatically
logged in as 'ubuntu' at boot.

Main usage is:

`./ubuntu.py  -n ubuntu -r 18 -s $((1024*1024)) --vnc --boot-script opendesktop.sh --gns3-appliance`

`--gns3-appliance` will shutdown the node when it's finished its install scripts,
copy the disk image to the current directory, and update the GNS3 appliance
file in `opendesktop.gns3a`
