#!/usr/bin/python3
#
# Script to fire up a GNS3 topology with a Cisco CSRv 1000, load
# a configuration on the router, notify the script when the router
# has booted, and test ping connectivity.
#
#                   +------+-------------------------------+
#                   | host |           GNS3                |
#  +------------+   |      |                               |
#  |            |   |   br0| +-----------+     +----------+|
#  | cisco-test |---|------|-|  Virtual  |-----|   Cisco  ||
#  |            |   |      | |   Switch  |     | CSRv 1000||
#  +------------+   |      | +-----------+     +----------+|
#                   +------+-------------------------------+
#
# The script depends on having IP connectivity with the virtual
# router.  GNS3 connects the virtual routers to an interface on the
# host (br0) that should have IP connectivity and a DHCP server
# running on it.

import gns3
import json
import argparse
try:
    import napalm
except ModuleNotFoundError:
    napalm = None


# Parse the command line options

parser = argparse.ArgumentParser(parents=[gns3.parser('cisco-test')], description='Start an Cisco node in GNS3')

parser._mutually_exclusive_groups[0].add_argument('cisco_image', metavar='FILENAME', nargs='?',
                    help='Cisco CSR1000v image filename')

args = parser.parse_args()

# Open GNS3 server

gns3_server, gns3_project = gns3.open_project_with_standard_options(args)

# If the user didn't specify a cloud image, use the first 'csr1000v' image on the server.
# If the user did specify an image, check to make sure it exists.

if args.cisco_image:
    assert args.cisco_image in gns3_server.images()
else:
    args.cisco_image = next(image for image in gns3_server.images() if image.startswith('csr1000v'))

# Create a GNS3 "cloud" for Internet access.
#
# It's done early in the script like this so that the gns3 library
# knows which interface we're using, because it might need that
# information to construct a notification URL.

cloud = gns3_project.cloud('Internet', args.interface, x=-200, y=200)

# CSR1000v

print("Building CSR1000v configuration...")

CSRv_config = """
int gig 1
  ip addr dhcp
  no shut

line vty 0 4
  transport input ssh
  login local

username cisco priv 15 password cisco

! A hostname is required for ssh to work

hostname R1

! This lets the "copy run URL" notification command work

file prompt quiet

! from https://community.cisco.com/t5/vpn-and-anyconnect/enabling-ssh-with-a-startup-config-or-similar/td-p/1636781
!
! This command doesn't work in the configuration file:
!     crypto key generate rsa modulus 768
! ...so run it using an EEM applet instead.
!
! Experience has shown that it doesn't work right away on boot,
! so introduce a five second delay before running it.

! from http://wiki.nil.com/Detect_DHCP_client_address_change_with_EEM_applet
!
! "The event routing network 0.0.0.0/0 type add protocol connected
!  event detector detects all additions of connected routes (the
!  0.0.0.0/0 mask indicates we want to catch all changes regardless of
!  the actual IP prefix)."
!
! We use this logic to make sure we've got a DHCP address before
! trying to handshake with the main script.  I also run the ssh key
! generation at this time, because I want to make sure that both
! events have happened (DHCP configuration and ssh key generation)
! before notifying the script that we're ready.

event manager applet send_notification authorization bypass
 event routing network 0.0.0.0/0 type add protocol connected ge 1
 action 10 cli command "enable"
 action 20 cli command "config t"
 action 30 cli command "no event manager applet send_notification"
 action 40 wait 5
 action 50 cli command "crypto key generate rsa modulus 1024"
 action 60 cli command "end"
 action 70 cli command "copy run {0}"

end
""".format(gns3_project.notification_url() + "CiscoCSR1000v")

# Configure a switch node and a CSR1000v with one interface

print("Configuring nodes...")

switch = gns3_project.switch('InternetSwitch', x=0, y=0)

images = {'iosxe_config.txt': CSRv_config.encode()}
config = {"symbol": ":/symbols/router.svg", "x" : 200, "y" : 200}
# Cisco CSR1000v can't seem to handle the scsi interface gns3.py uses as its default
properties = {"ram": 4*1024, "hda_disk_interface": 'ide'}

cisco = gns3_project.create_qemu_node('CiscoCSR1000v', args.cisco_image, images=images, config=config, properties=properties)

gns3_project.link(cisco, 0, switch)
gns3_project.link(cloud, 0, switch)

# Can't make this check before a link has been connected to the cloud

cloud_status = gns3_project.node(cloud['node_id'])['status']
if cloud_status != 'started':
    print(f"Cloud node reports status '{cloud_status}'; interface '{args.interface}' might be unavailable")

gns3_project.start_nodes(cisco)

if napalm:
    print("Running ping test...")

    dev = napalm.get_network_driver('ios')

    for hostname,addr in gns3_project.httpd.instances_reported.items():
        device = dev(hostname=addr, username='cisco', password='cisco')
        device.open()
        print(json.dumps(device.ping(gns3_project.get_local_ip()), indent=4))
