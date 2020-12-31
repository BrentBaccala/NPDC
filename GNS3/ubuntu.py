#!/usr/bin/python3
#
# Script to start a GNS3 Ubuntu virtual machine named "ubuntu" on
# the existing project "Virtual Network".  These names can be changed
# with command line options, with also let the user select the Ubuntu
# release, the virtual memory size, the virtual disk size, and the
# number of CPUs.
#
# Can be passed a '-d' option to delete an existing "ubuntu" VM.
#
# We use an Ubuntu cloud image that comes with the cloud-init package
# pre-installed, so that we can construct a configuration script and
# provide it to the VM on a virtual CD-ROM.
#
# The current script installs SSH public key to authenticate me in to
# the "ubuntu" account and POSTs a notification message back to this
# script once the boot process is complete.
#
# Resizing the virtual disk from its 2 GB default size now requires a
# custom gns3server since the released server can't resize disks
# before the instance's first boot.
#
# I'd also like to set the DHCP client to use a pre-set client
# identifier so that the VM always boots onto the same IP address,
# but cloud-init doesn't seem to have any option to support that.

import sys
import requests
from requests.auth import HTTPBasicAuth
import json
import yaml
import os
import time
import tempfile
import pprint

import socket
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer

import argparse

import subprocess

import configparser

PROP_FILE = os.path.expanduser("~/.config/GNS3/2.2/gns3_server.conf")

cloud_images = {
    20: 'ubuntu-20.04-server-cloudimg-amd64.img',
    18: 'ubuntu-18.04-server-cloudimg-amd64.img'
}

# Parse the command line options

parser = argparse.ArgumentParser(description='Start an Ubuntu node in GNS3')
parser.add_argument('-d', '--delete', action="store_true",
                    help='delete the node instead of creating it')
parser.add_argument('-n', '--name', default='ubuntu',
                    help='name of the Ubuntu node (default "ubuntu")')
parser.add_argument('-p', '--project', default='Virtual Network',
                    help='name of the GNS3 project (default "Virtual Network")')
parser.add_argument('-c', '--cpus', type=int, default=1,
                    help='number of virtual CPUs (default 1)')
parser.add_argument('-m', '--memory', type=int, default=4096,
                    help='MBs of virtual RAM (default 4096)')
parser.add_argument('-s', '--disk', type=int, default=2048,
                    help='MBs of virtual disk (default 2048)')
parser.add_argument('-r', '--release', type=int, default=20,
                    help='Ubuntu major release number (default 20)')
parser.add_argument('--vnc', action="store_true",
                    help='use a VNC console (default is text console)')
parser.add_argument('--ls', action="store_true",
                    help='list running nodes')
parser.add_argument('-q', '--query', action="store_true",
                    help='query the existence of the nodes')
parser.add_argument('-v', '--verbose', action="store_true",
                    help='print the JSON node structure')
args = parser.parse_args()

cloud_image = cloud_images[args.release]

# Obtain the credentials needed to authenticate ourself to the GNS3 server

config = configparser.ConfigParser()
config.read(PROP_FILE)

gns3_server = config['Server']['host'] + ":" + config['Server']['port']
auth = HTTPBasicAuth(config['Server']['user'], config['Server']['password'])

# Find the GNS3 project called project_name

print("Finding project...")

url = "http://{}/v2/projects".format(gns3_server)

result = requests.get(url, auth=auth)
result.raise_for_status()

project_id = None

for project in result.json():
    if project['name'] == args.project:
        project_id = project['project_id']

if not project_id:
    print("Couldn't find project '{}'".format(args.project))
    exit(1)

print("'{}' is {}".format(args.project, project_id))

# Get the existing nodes and links in the project.
#
# We'll need this information to find a free port on a switch
# to connect our new gadget to.

url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

result = requests.get(url, auth=auth)
result.raise_for_status()

nodes = result.json()

if args.ls:
    for node in nodes:
        print(node['name'])
    exit(0)

url = "http://{}/v2/projects/{}/links".format(gns3_server, project_id)

result = requests.get(url, auth=auth)
result.raise_for_status()

links = result.json()

# Does a node with this name already exist in the project?
#
# GNS3 sometimes appends a number to the node name, so we identify our
# node as any node whose name begins with args.name.

ubuntus = [n['node_id'] for n in nodes if n['name'].startswith(args.name)]

if len(ubuntus) > 0:
    print("{} already exists as node {}".format(args.name, ubuntus[0]))
    if args.verbose:
        pprint.pprint(next(n for n in nodes if n['name'].startswith(args.name)))
    if args.delete:
        print("deleting {}...".format(ubuntus[0]))
        node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, ubuntus[0])
        result = requests.delete(node_url, auth=auth)
        result.raise_for_status()
        exit(0)
    exit(1)

if args.delete:
    print("Found no {} node to delete".format(args.name))
    exit(1)

if args.query:
    if len(ubuntus) == 0:
        print("No matching nodes")
    exit(1)

# Find switches and find the first unoccupied port on a switch.
#
# We identify switches by looking for the string "switch" in the
# name of the SVG file used for the node's icon.
#
# We deliberately skip the first port, since on a Cisco nx9k, adapter 0 is
# the management interface.

switches = [n['node_id'] for n in nodes if 'switch' in n['symbol']]
switch_ports = [(p['adapter_number'], p['port_number']) for n in nodes if 'switch' in n['symbol'] for p in n['ports']]

adapters = [a for l in links for a in l['nodes']]
occupied_ports = [(a['adapter_number'], a['port_number']) for a in adapters if a['node_id'] in switches]

first_unoccupied_port = next(p for p in sorted(switch_ports)[1:] if p not in occupied_ports)

# This will return the local IP address that the script uses to
# connect to the GNS3 server.  We need this to tell the instance
# how to connect back to the script, and if we've got multiple
# interfaces, multiple DNS names, and multiple IP addresses, it's a
# bit unclear which one to use.
#
# from https://stackoverflow.com/a/28950776/1493790

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # doesn't even have to be reachable
    s.connect((gns3_server.split(':')[0], 1))
    IP = s.getsockname()[0]
    s.close()
    return IP

script_ip = get_ip()

# Start an HTTP server running that will receive notifications from
# the instance after its completes its boot.
#
# This assumes that the virtual topology will have connectivity with
# the host running this script.
#
# We keep a set of which instances have reported in, and a condition
# variable is used to signal our main thread when they report.

instances_reported = set()
instance_report_cv = threading.Condition()

class RequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = self.headers['Content-Length']
        self.send_response_only(100)
        self.end_headers()

        content = self.rfile.read(int(length))
        # print(content.decode('utf-8'))

        with instance_report_cv:
            if not self.client_address[0] in instances_reported:
                instances_reported.add(self.client_address[0])
                instance_report_cv.notify()

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

# Create an ISO image containing the boot configuration and upload it
# to the GNS3 project.  We write the config to a temporary file,
# convert it to ISO image, then post the ISO image to GNS3.

print("Building cloud-init configuration...")

meta_data = {'instance-id' : 'ubuntu',
             'local-hostname' : args.name
}

user_data = {'ssh_authorized_keys':
             [ "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCj6Vc0dUbmLEXByfgwtbG0teq+lhn1ZeCpBp/Ll+yapeTbdP0AuA9iZrcIi4O25ucy+VaZDutj2noNvkcq8dPrCmveX0Zxbylia7rNbd91DPU/94JRidElJPzB5eueObqiVWNWu1cGP0WdaHbecWy0Xu4fq+FqJn3z99Cg4XDYVsfP9avin6McHAaYItTmZHAuHgfL6hJCw4Ju0I7OMAlXgeb9S50nYpzN8ItbRmNQDZC3wdPs5iTd0LgGG/0P7ixhTWDSg5DeQc6JJ2rYezyzc1Lek3lQuBK6FiuvEyd99H2FrowN0b/n1pTQd//pq1G0AcGiwl0ttZ5i2HMe8sab baccala@max",
               "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCrP+7mipq2WDogHqJ4So8F4fwPNj87sfOuFh6c3Md5SHg3B3U29Mqu+MgVz9aZ60Nsfr5/blZA7Kjx0GeMHiZHnVf8hS4R8vx066Ck479ZL+6kXDijkxBTPQoTfpuRsqN+vhX5pS+WAPfgKl6pcRtonTMBY1dh/B+KQBhQ2KzdydpDz7dLQRmuKIKNvyNhs4CRS0P8oFZlmuvDjdmvkmKbyp06sZAFHbbWhLs0PHobItNDviwRrBg59tS9Dr40raGUrp3SIsaQTIT56zQAdVB36iZDqYbUf/rCizIcsoCWB76LW7JMvJot1NVKtN9D56ZCgXhW4IJ1dWw2bPY+6lz3 BrentBaccala@max",
               "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCzuN81Hcxd5wpfT8JFzhFXG0JoyOpLAOGl6r0bb4iTt86VJMfvByJorKHVWi/Wp1qRqzAAeAnlSKRTm7CeIy744Y1/iaWQwDMkS+Sjwhib104sqM8EIFVVeiorvwPa8GbpdgxS6H6s5zO4mlnW5MdiV67jlyd0xWc3jDWCqwGLJBgYrJEuztQ5hlLDfliDSs8ZpSijgkROII2yORuU+YuVkHgFcmRDXnIKq7iL5xKW89KGSU8yOi6v1iW9xccs0m5hB35B3zX8Kha25dhBpVXrLlvP8Xf2y8MYIoYVaYurLLqSVmRoGMXOnaXxw3iX9ERMvuhj0PIPNPOK7ZJvN3en baccala@samsung",
               "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC9S97gTUYfGLFR0ao29wcasi0FDPNQfocwogyXXZoo9aPyEQE2UzsG8geqlh34YVa5yP3Y5IELAfhEaIesM7tDktISIRXEqcGthP9NSlPm/nGeNq2xeUKoHw9gfB4UkT1sLPz2unQB9MK532O+blJqdSVXsAbi7atXqx+P16faz9+VU+uYP923s790tw6X27Udpg50Ie84DchmOup/lRXlemOb6Q3iz3bVyOg1/7KLwg4L7IGvyYwmtrhO6BAdZRGwYhptGHBovSXd+YoDUsEjul2KKsmvzWK7sYFiwE9ctxTZB2UT3KgughmWCzftoErG/LYZj/PgPHgiTGgRvVeF baccala@osito"],

             'phone_home': {'url': notification_url},
}

meta_data_file = tempfile.NamedTemporaryFile(delete = False)
meta_data_file.write(yaml.dump(meta_data).encode('utf-8'))
meta_data_file.close()

user_data_file = tempfile.NamedTemporaryFile(delete = False)
user_data_file.write(("#cloud-config\n" + yaml.dump(user_data)).encode('utf-8'))
user_data_file.close()

import subprocess

genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                       "-relaxed-filenames", "-V", "cidata", "-graft-points",
                       "meta-data={}".format(meta_data_file.name),
                       "user-data={}".format(user_data_file.name)]

genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

isoimage = genisoimage_proc.stdout.read()

os.remove(meta_data_file.name)
os.remove(user_data_file.name)

print("Uploading cloud-init configuration...")

file_url = "http://{}/v2/projects/{}/files/config.iso".format(gns3_server, project_id)
result = requests.post(file_url, auth=auth, data=isoimage)
result.raise_for_status()

# Configure an Ubuntu cloud node

print("Configuring Ubuntu cloud node...")

url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

ubuntu_node = {
        "compute_id": "local",
        "name": args.name,
        "node_type": "qemu",
        "properties": {
            "adapters": 1,
            "adapter_type" : "virtio-net-pci",
            "hda_disk_image": cloud_image,
            "cdrom_image" : "config.iso",
            "qemu_path": "/usr/bin/qemu-system-x86_64",
            "cpus": args.cpus,
            "ram": args.memory
        },

        "symbol": ":/symbols/qemu_guest.svg",
        "x" : 0,
        "y" : 0
    }

if args.vnc:
    ubuntu_node['console_type'] = 'vnc'

result = requests.post(url, auth=auth, data=json.dumps(ubuntu_node))
result.raise_for_status()
ubuntu = result.json()

# LINK TO SWITCH

print("Configuring link to switch...")

url = "http://{}/v2/projects/{}/links".format(gns3_server, project_id)

link_obj = {'nodes' : [{'adapter_number' : 0,
                        'port_number' : 0,
                        'node_id' : ubuntu['node_id']},
                       {'adapter_number' : first_unoccupied_port[0],
                        'port_number' : first_unoccupied_port[1],
                        'node_id' : switches[0]}]}

result = requests.post(url, auth=auth, data=json.dumps(link_obj))
result.raise_for_status()

# RESIZE THE NODE'S DISK (IF REQUESTED)

if args.disk > 2048:

    print("Extending disk by {} MB...".format(args.disk - 2048))

    url = "http://{}/v2/compute/projects/{}/qemu/nodes/{}/resize_disk".format(gns3_server, project_id, ubuntu['node_id'])

    resize_obj = {'drive_name' : 'hda', 'extend' : args.disk - 2048}

    result = requests.post(url, auth=auth, data=json.dumps(resize_obj))
    result.raise_for_status()

# START NODE RUNNING

print("Starting the node...")

project_start_url = "http://{}/v2/projects/{}/nodes/{}/start".format(gns3_server, project_id, ubuntu['node_id'])
result = requests.post(project_start_url, auth=auth)
result.raise_for_status()

print("Waiting for node to start...")

node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, ubuntu['node_id'])
result = requests.get(node_url, auth=auth)
result.raise_for_status()
while result.json()['status'] != 'started':
    time.sleep(1)
    result = requests.get(node_url, auth=auth)
    result.raise_for_status()

print("Waiting for node to finish booting...")

with instance_report_cv:
    while len(instances_reported) == 0:
        instance_report_cv.wait()

# print(instances_reported)

httpd.shutdown()
