#!/usr/bin/python3
#
# Script to fire up a GNS3 topology with a Cisco CSRv 1000, load
# a configuration on the router, notify the script when the router
# has booted, and test ping connectivity.
#
#                  +------+-------------------------------+
#                  | host |           GNS3                |
#  +-----------+   |      |                               |
#  |           |   |   br0| +-----------+     +----------+|
#  | ping-test |---|------|-|  Virtual  |-----|   Cisco  ||
#  |           |   |      | |   Switch  |     | CSRv 1000||
#  +-----------+   |      | +-----------+     +----------+|
#                  +------+-------------------------------+
#
# The script depends on having IP connectivity with the virtual
# router.  GNS3 connects the virtual routers to an interface on the
# host (br0) that should have IP connectivity and a DHCP server
# running on it.

import gns3

import argparse

#import napalm

# Which interface on the bare metal system is used to access the Internet from GNS3?
#
# It should be either a routed virtual link to the bare metal system, or
# a bridged interface to a physical network device.

INTERNET_INTERFACE = 'veth'

# Parse the command line options

parser = argparse.ArgumentParser(description='Start an Ubuntu node in GNS3')
parser.add_argument('-H', '--host',
                    help='name of the GNS3 host')
parser.add_argument('-p', '--project', default='ping-test',
                    help='name of the GNS3 project (default "ping-test")')
parser.add_argument('-I', '--interface', default=INTERNET_INTERFACE,
                    help=f'network interface for Internet access (default "{INTERNET_INTERFACE}")')
parser.add_argument('client_image', metavar='FILENAME', nargs='?',
                    help='client image to test')
args = parser.parse_args()

# Create a new GNS3 project called 'ping-test'
#
# The only required field for a new GNS3 project is 'name'
#
# This will error out with a 409 Conflict if 'ping-test' already exists

#print("Creating project...")

gns3_server = gns3.Server(host=args.host)

gns3_project = gns3_server.project(args.project, create=True)

gns3_project.open()

# Create an ISO image containing the boot configuration and upload it
# to the GNS3 project.  We write the config to a temporary file,
# convert it to ISO image, then post the ISO image to GNS3.

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
 action 50 cli command "crypto key generate rsa modulus 768"
 action 60 cli command "end"
 action 70 cli command "copy run {0}"

end
""".format(gns3_project.notification_url)

# Configure a cloud node, a switch node, and a CSRv with one interface

print("Configuring nodes...")

switch = gns3_project.switch('InternetSwitch', x=0, y=0)

cisco_image = "csr1000v-universalk9.16.07.01-serial.qcow2"
images = {'iosxe_config.txt': CSRv_config.encode()}
config = {"symbol": ":/symbols/router.svg",
              "x" : 200,
              "y" : 200
}
properties = {"ram": 4*1024, "hda_disk_interface": None}
cisco = gns3_project.create_qemu_node('CiscoCSR1000v', cisco_image, images=images, config=config, properties=properties)

cloud = gns3_project.cloud('Internet', args.interface, x=-200, y=200)

gns3_project.link(cisco, 0, switch)
gns3_project.link(cloud, 0, switch)

gns3_project.start_node(cisco)

#print("Running ping test...")

#dev = napalm.get_network_driver('ios')

#for hostname in routers_reported:
#    device = dev(hostname=hostname, username='cisco', password='cisco')
#    device.open()
#    print(json.dumps(device.ping(script_ip), indent=4))
