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

import requests
import json

import math

import subprocess

import threading
from http.server import BaseHTTPRequestHandler,HTTPServer

import napalm

# Don't use localhost for gns3_server, even if the server is running
# on the same host as the script, since we use gns3_server in the next
# section of code to determine which of our IP addresses we should
# pass to the router, and we surely don't want 127.0.0.1.

gns3_server = "blade7:3080"
host_interface = "br0"

# This will return the local IP address that the script uses to
# connect to the GNS3 server.  We need this to tell the Cisco CSRv
# how to connect back to the script, and if we've got multiple
# interfaces, multiple DNS names, and multiple IP addresses, it's a
# bit unclear which one to use.
#
# from https://stackoverflow.com/a/28950776/1493790

import socket

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # doesn't even have to be reachable
    s.connect((gns3_server.split(':')[0], 1))
    IP = s.getsockname()[0]
    s.close()
    return IP

script_ip = get_ip()

# Start an HTTP server running that will receive notifications from
# the Cisco CSRv's after they complete their boot.
#
# This assumes that the virtual topology will have connectivity with
# the host running this script.
#
# We keep a set of which routers have reported in, and a condition
# variable is used to signal our main thread when they report.

routers_reported = set()
router_report_cv = threading.Condition()

class RequestHandler(BaseHTTPRequestHandler):
    def do_PUT(self):
        length = self.headers['Content-Length']
        self.send_response_only(100)
        self.end_headers()

        content = self.rfile.read(int(length))
        # print(content.decode('utf-8'))

        with router_report_cv:
            if not self.client_address[0] in routers_reported:
                routers_reported.add(self.client_address[0])
                router_report_cv.notify()

        self.send_response(200)
        self.end_headers()

server_address = ('', 0)
httpd = HTTPServer(server_address, RequestHandler)

notification_url = "http://{}:{}/".format(script_ip, httpd.server_port)

# Catch uncaught exceptions and shutdown the httpd server that we're
# about to start.
#
# from https://stackoverflow.com/a/6598286/1493790

import sys
import pdb

def my_except_hook(exctype, value, traceback):
    httpd.shutdown()
    pdb.set_trace()
    sys.__excepthook__(exctype, value, traceback)
sys.excepthook = my_except_hook

threading.Thread(target=httpd.serve_forever).start()

# Create a new GNS3 project called 'ping-test'
#
# The only required field for a new GNS3 project is 'name'
#
# This will error out with a 409 Conflict if 'ping-test' already exists

print("Creating project...")

new_project = {'name': 'ping-test', 'auto_close' : False}

url = "http://{}/v2/projects".format(gns3_server)

result = requests.post(url, data=json.dumps(new_project))
result.raise_for_status()

my_project = result.json()

# Register an exit handler to remove the project when this script
# exits, no matter how it exits.

def remove_project(project_id):
    url = "http://{}/v2/projects/{}".format(gns3_server, project_id)
    result = requests.delete(url)
    result.raise_for_status()

import atexit
atexit.register(remove_project, my_project['project_id'])

# Create an ISO image containing the boot configuration and upload it
# to the GNS3 project.  We write the config to a temporary file,
# convert it to ISO image, then post the ISO image to GNS3.
#
# This approach doesn't work with the standard GNS3 implementation,
# which doesn't allow images to be loaded from the project directory.
#
# Requires a patched gns3-server.

print("Building CSRv configuration...")

CSRv_config = """
int gig 1
  ip addr dhcp
  no shut

line vty 0 4
  transport input ssh
  login local

username cisco priv 15 password cisco

ip route 0.0.0.0 0.0.0.0 http

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
""".format(notification_url)

import os
import tempfile

config_file = tempfile.NamedTemporaryFile(delete = False)

config_file.write(CSRv_config.encode('utf-8'))
config_file.close()

import subprocess

genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                       "-graft-points", "iosxe_config.txt={}".format(config_file.name)]

genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE)

isoimage = genisoimage_proc.stdout.read()

os.remove(config_file.name)

print("Uploading CSRv configuration...")

file_url = "http://{}/v2/projects/{}/files/config.iso".format(gns3_server, my_project['project_id'])
result = requests.post(file_url, data=isoimage)
result.raise_for_status()

# Configure a cloud node, a switch node, and a CSRv with one interface

print("Configuring nodes...")

url = "http://{}/v2/projects/{}/nodes".format(gns3_server, my_project['project_id'])

cloud_node = {
        "compute_id": "local",
        "name": "Cloud",
        "node_type": "cloud",

        "symbol": ":/symbols/cloud.svg",
        "x" : -300,
        "y" : 0,
    }

cloud_result = requests.post(url, data=json.dumps(cloud_node))
cloud_result.raise_for_status()
cloud = cloud_result.json()

switch_node = {
        "compute_id": "local",
        "name": "Switch",
        "node_type": "ethernet_switch",

        "symbol": ":/symbols/ethernet_switch.svg",
        "x" : 0,
        "y" : 0,
    }

switch_result = requests.post(url, data=json.dumps(switch_node))
switch_result.raise_for_status()
switch = switch_result.json()

CSRv_node = {
        "compute_id": "local",
        "name": "CiscoCSR1000v16.7.1(a)",
        "node_type": "qemu",
        "properties": {
            "adapters": 1,
            "adapter_type" : "virtio-net-pci",
            "hda_disk_image": "csr1000v-universalk9.16.07.01-serial.qcow2",
            "cdrom_image" : "config.iso",
            "qemu_path": "/usr/bin/qemu-system-x86_64",
            "ram": 3072
        },

        "symbol": ":/symbols/router.svg",
        "x" : 300,
        "y" : 0
    }

result = requests.post(url, data=json.dumps(CSRv_node))
result.raise_for_status()
CSRv = result.json()

# LINKS

print("Configuring links...")

url = "http://{}/v2/projects/{}/links".format(gns3_server, my_project['project_id'])

# find host_interface ('br0') port in cloud object

br0 = [port for port in cloud['ports'] if port['short_name'] == host_interface][0]

# Link the cloud to the switch

link_obj = {'nodes' : [{'adapter_number' : br0['adapter_number'],
                        'port_number' : br0['port_number'],
                        'node_id' : cloud['node_id']},
                       {'adapter_number' : 0,
                        'port_number' : 0,
                        'node_id' : switch['node_id']}]}

result = requests.post(url, data=json.dumps(link_obj))
result.raise_for_status()

# Link the first interface of the CSRv to the switch

link_obj = {'nodes' : [{'adapter_number' : 0,
                        'port_number' : 0,
                        'node_id' : CSRv['node_id']},
                       {'adapter_number' : 0,
                        'port_number' : 1,
                        'node_id' : switch['node_id']}]}

result = requests.post(url, data=json.dumps(link_obj))
result.raise_for_status()

# START THE NODES

print("Starting nodes...")

project_start_url = "http://{}/v2/projects/{}/nodes/start".format(gns3_server, my_project['project_id'])
result = requests.post(project_start_url)
result.raise_for_status()

# WAIT FOR NODES TO BOOT
#
# One way to do this if is the DHCP servers supports notifications of
# new clients.
#
# The ISC Kea server probably can do that, in a somewhat roundabout
# way.  It can use MySQL as a backend, and you can set a MySQL trigger
# for when a new lease appears in the database.  You probably need to
# add an extension to MySQL to let it run shell scripts, which run as
# the MySQL database user, so the script could do something like
# delete the entire database.  Or configure an extension to run the
# scripts as 'nobody'.  Another possibility is to enhance Kea so that
# it directly supports notifications.
#
# Instead, I use the EEM applet on the router to send the notification.
# This has the advantage of ensuring that the router has generated an
# RSA key and can receive SSH connections.

print("Waiting for nodes to boot...")

with router_report_cv:
    while len(routers_reported) == 0:
        router_report_cv.wait()

# TOPOLOGY UP AND RUNNING

print("Running ping test...")

dev = napalm.get_network_driver('ios')

for hostname in routers_reported:
    device = dev(hostname=hostname, username='cisco', password='cisco')
    device.open()
    print(json.dumps(device.ping(script_ip), indent=4))

# Shutdown the http server thread, break down the GNS3 project, and exit

print("Exiting...")

httpd.shutdown()
