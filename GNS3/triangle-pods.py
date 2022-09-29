#!/usr/bin/python3
#
# Script to fire up a GNS3 topology with sets of Cisco CSRv 1000s,
# load a configuration on the router, and notify the script when the
# routers have booted.
#
# Each set has three routers arranged in the following topology:
#
#                  +------+--------------------------------------------------+
#                  | host |           GNS3    +------------------+           |
#                  |      |            -------|GE0    Cisco   GE1|------\    |
#                  |      |           /       |     CSRv 1000 GE2|---\  |    |
#  +-----------+   |      |          /        +------------------+   |  |    |
#  | triangle  |   |   br0| +-----------+     +------------------+   |  |    |
#  |   pods    |---|------|-|  Virtual  |-----|GE0    Cisco   GE1|---/  |    |
#  |           |   |      | |   Switch  |     |     CSRv 1000 GE2|---\  |    |
#  +-----------+   |      | +-----------+     +------------------+   |  |    |
#                  |      |          \        +------------------+   |  |    |
#                  |      |           \-------|GE0    Cisco   GE1|---/  |    |
#                  |      |                   |     CSRv 1000 GE2|------/    |
#                  |      |                   +------------------+           |
#                  +------+--------------------------------------------------+
#
# The script depends on having IP connectivity with the virtual
# router.  GNS3 connects the virtual routers to an interface on the
# host that should have IP connectivity and a DHCP server
# running on it.
#
# Running four or five pods on a C200 has performance problems, even though
# the machine seems like it has enough RAM (96 GB) and CPU (12 cores).
# Watch the bare memory virtual memory utilization on top.  The CSRv's
# seem to like to scan their memory as they boot, so you can watch
# the resident set size to see their progress.

import gns3
import math
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
parser.add_argument('-p', '--project', default='triangle-pods',
                    help='name of the GNS3 project (default "triangle-pods")')
parser.add_argument('-n', '--npods', type=int, default=5,
                    help='number of pods to create (default 5)')
parser.add_argument('-I', '--interface', default=INTERNET_INTERFACE,
                    help=f'network interface for Internet access (default "{INTERNET_INTERFACE}")')
group = parser.add_mutually_exclusive_group()
group.add_argument('--delete-everything', action="store_true",
                    help='delete everything in the project instead of creating it')
group.add_argument('cisco_image', metavar='FILENAME', nargs='?',
                    help='client image to test')
args = parser.parse_args()

# Open the GNS3 server

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

# The CSRv's

def mkhostname(pod, router):
    return "{}{}".format(pod, router)

hostnames = [mkhostname(pod, router) for pod in range(1,args.npods+1) for router in ["a", "b", "c"]]

hostname_x = {}
hostname_y = {}

for pod in range(1,args.npods+1):
    for n, router in enumerate(["a", "b", "c"]):
        hostname_x[mkhostname(pod, router)] = -int(300 * math.cos((pod + .5) * 2*math.pi / args.npods)) \
            + int(50 * math.cos(n * 2*math.pi / 3))
        hostname_y[mkhostname(pod, router)] = int(300 * math.sin((pod + .5) * 2*math.pi / args.npods)) \
            + int(50 * math.sin(n * 2*math.pi / 3))

print("Building CSRv configuration...")

def CSRv_config(hostname, notification_url):
    return f"""
int gig 1
  ip addr dhcp
  no shut

line vty 0 4
  transport input ssh
  login local

username cisco priv 15 password cisco

! A hostname is required for ssh to work

hostname {hostname}

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
 action 70 cli command "copy run {notification_url}"

end
"""

print("Configuring nodes...")

nports = int((3 * args.npods)/8 + 1) * 8
switch = gns3_project.switch(f'InternetSwitch', ethernets=nports, x=0, y=0)

CSRv = {}

for hostname in hostnames:
    images = {'iosxe_config.txt': CSRv_config(hostname, gns3_project.notification_url + hostname).encode()}
    config = {"symbol": ":/symbols/router.svg", "x" : hostname_x[hostname], "y" : hostname_y[hostname]}
    # Cisco CSR1000v can't seem to handle the scsi interface gns3.py uses as its default
    properties = {"ram": 4*1024, "hda_disk_interface": 'ide', 'adapters': 3}

    CSRv[hostname] = gns3_project.create_qemu_node(hostname, args.cisco_image, images=images, config=config, properties=properties)

cloud = gns3_project.cloud('Internet', args.interface, x=-400, y=0)

# Link the cloud to the switch

gns3_project.link(cloud, 0, switch)

# Link the first interface of each CSRv to the switch

for hostname in hostnames:
    gns3_project.link(CSRv[hostname], 0, switch)

# Link the second and third interfaces of each CSRv pair together

for pod in range(1, args.npods+1):
    for (i,j) in [('a', 'b'), ('b', 'c'), ('c', 'a')]:
        router1 = mkhostname(pod, i)
        router2 = mkhostname(pod, j)
        gns3_project.link(CSRv[router1], 1, CSRv[router2], 2)

# START THE NODES

print("Starting nodes...")

gns3_project.start_nodes(*CSRv)

# TOPOLOGY UP AND RUNNING

print("Running ping tests...")

dev = napalm.get_network_driver('ios')

for hostname,addr in gns3_project.httpd.instances_reported.items():
    device = dev(hostname=addr, username='cisco', password='cisco')
    device.open()
    print(json.dumps(device.ping(gns3_project.local_ip), indent=4))

for hostname in hostnames:
    print("{:16} IN A {}".format(hostname, gns3_project.httpd.instances_reported[hostname]))
