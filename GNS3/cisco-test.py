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
import napalm
import argparse

# Which interface on the bare metal system is used to access the Internet from GNS3?
#
# It should be either a routed virtual link to the bare metal system, or
# a bridged interface to a physical network device.

INTERNET_INTERFACE = 'veth'

# Parse the command line options

parser = argparse.ArgumentParser(description='Start an Cisco test network in GNS3')
parser.add_argument('-H', '--host',
                    help='name of the GNS3 host')
parser.add_argument('-p', '--project', default='cisco-test',
                    help='name of the GNS3 project (default "cisco-test")')
parser.add_argument('-I', '--interface', default=INTERNET_INTERFACE,
                    help=f'network interface for Internet access (default "{INTERNET_INTERFACE}")')
group = parser.add_mutually_exclusive_group()
group.add_argument('--ls', action="store_true",
                    help='list running nodes')
group.add_argument('--delete-everything', action="store_true",
                    help='delete everything in the project instead of creating it')
group.add_argument('cisco_image', metavar='FILENAME', nargs='?',
                    help='client image to test')
args = parser.parse_args()

# Open GNS3 server

gns3_server = gns3.Server(host=args.host)

# If the user didn't specify a cloud image, use the first 'csr1000v' image on the server.
# If the user did specify an image, check to make sure it exists.

if args.cisco_image:
    assert args.cisco_image in gns3_server.images()
else:
    args.cisco_image = next(image for image in gns3_server.images() if image.startswith('csr1000v'))

# Open or create a GNS3 project

gns3_project = gns3_server.project(args.project, create=True)

gns3_project.open()

if args.delete_everything:
    gns3_project.delete_everything()
    exit(0)

if args.ls:
    print([n['name'] for n in gns3_project.nodes()])
    exit(0)

print("Building CSRv configuration...")

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
""".format(gns3_project.notification_url + "CiscoCSR1000v")

# Configure a cloud node, a switch node, and a CSRv with one interface

print("Configuring nodes...")

switch = gns3_project.switch('InternetSwitch', x=0, y=0)

images = {'iosxe_config.txt': CSRv_config.encode()}
config = {"symbol": ":/symbols/router.svg", "x" : 200, "y" : 200}
# Cisco CSR1000v can't seem to handle the scsi interface gns3.py uses as its default
properties = {"ram": 4*1024, "hda_disk_interface": 'ide'}

cisco = gns3_project.create_qemu_node('CiscoCSR1000v', args.cisco_image, images=images, config=config, properties=properties)

cloud = gns3_project.cloud('Internet', args.interface, x=-200, y=200)

gns3_project.link(cisco, 0, switch)
gns3_project.link(cloud, 0, switch)

gns3_project.start_nodes(cisco)

print("Running ping test...")

dev = napalm.get_network_driver('ios')

for hostname,addr in gns3_project.httpd.instances_reported.items():
    device = dev(hostname=addr, username='cisco', password='cisco')
    device.open()
    print(json.dumps(device.ping(gns3_project.local_ip), indent=4))
