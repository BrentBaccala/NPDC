# Scripts to work with GNS3 as a virtual network orchestrator

**Note**: gns3 is very picky about matching GUI client and server versions.  I typically put dpkg holds
on the gns3 packages, since otherwise an apt upgrade on my laptop requires both an apt upgrade on my
gns3 server *and* restarting the gns3 server, which implies stopping and restarting all of the running VMs.

## Setup

1. gns3 is not in the standard Ubuntu distribution, but the gns3 team maintains an Ubuntu Personal Package Archive (PPA).
   Also, I like to run the gns3 as system service as its own user.  Internet access also needs to be configured.
   A script is provided to configure all of this.

   `install-gns3.sh` will install a new user 'gns3' that operates a
   gns3server and keeps all of the gns3 configuration and virtual
   drives (which can be quite large) in /home/gns3.  A virtual network interface called 'veth'
   will also be created, suitable for use by gns3 cloud nodes, with the
   bare metal machine running this script configured as a DHCP server
   and NAT gateway.  The gns3 PPA is added as an apt repository, and
   necessary packages are installed.

   The script does assume that the gns3 user doesn't exist, and it appends
   to any existing dhcpd configuration.

   When it is done, it prints the password used to access the gns3 REST API.

1. Install the gns3 GUI: `sudo apt install gns3-gui`

1. You should now be able to start the gns3 GUI and access the gns3 server.  Select "Run applications on a remote server" and use the credentials (gns3/PASSWORD) obtained above to access it.

   I think you want "Run applications on a remote server" even if you're running the server on your local machine, because the `gns3-bbb.py` script needs access to the server's REST API.

1. Configure authentication to gns3-server in either `~/gns3_server.conf` or `~/.config/GNS3/2.2/gns3_server.conf`.

   If you used the gns3 GUI to test access to the server, you already have a suitable `~/.config/GNS3/2.2/gns3_server.conf`.

1. Download a current Ubuntu 20 cloud image from Canonical:

   `wget https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-amd64.img`

1. Upload to the gns3 server using `upload-image.py`:

   `./upload-image.py ubuntu-20.04-server-cloudimg-amd64.img`

   The most uncommon Python3 package that this script uses is `python3-requests-toolbelt`.
   You may need to install it with apt.

   If this step works, then you have REST API access to the GNS3 server.

1. You should now be able to boot an Ubuntu instance like this:

   `./ubuntu-test.py --debug`

   Double-click on the icon that appears in the GUI to access the instance's console.

   The `--debug` option adds a login with username `ubuntu` and password `ubuntu`.

   Login and verify, in particular, that networking is working properly.  You should have Internet access.

1. Build a GUI image using `ubuntu.py`:

   `./ubuntu.py -r 20 -s $((1024*1024)) -m 1024 --boot-script opendesktop.sh --gns3-appliance`

   This step adds the GUI packages to the Ubuntu 20 cloud image and creates a new cloud image used for the test clients. It takes about half an hour.

1. Upload the GUI image to the gns3 server using `upload-image.py`

1. Add the GUI image to the appliance file `opendesktop.gns3a` like this:
   `./add-appliance.py -n 20 FILENAME`

1. Import the appliance file into the GNS3 GUI, and you will have a drag-and-drop Ubuntu instance
   with a full GNOME desktop.

## Directory of scripts

1. `gns3.py` is a library used by scripts

1. `upload-image.py` uploads images to the GNS3 server

1. `ubuntu-test.py` starts a simple Ubuntu image with a cloud and a switch to provide Internet connectivity

1. Likewise, `cisco-test.py` starts a simple Cisco CSR1000v.  You must upload the Cisco image yourself.

1. `triangle-pods.py` starts a number of Cisco CSRV1000v pods, each with three routers connected in a mesh.

1. `ubuntu.py` starts a node on a GNS3 server.  It will be configured by cloud-init to run some startup scripts.  The main script right now is opendesktop.sh, which installs everything needed for a basic GNOME desktop that is automatically logged in as 'ubuntu' at boot.

   Main usage is:

   `./ubuntu.py  -n ubuntu -r 18 -s $((1024*1024)) --vnc --boot-script opendesktop.sh --gns3-appliance`

   `--gns3-appliance` will shutdown the node when it's finished its install scripts and copy the disk image to the current directory.  Expect the entire process to take about half an hour.

1. `add-appliance.py` adds an appliance image to the appliance file `opendesktop.gns3a`, which can then be imported into GNS3's GUI.
