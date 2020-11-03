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
# The current script installs a pre-generated host key to identify the
# VM, installs an SSH public key to authenticate me in to the "ubuntu"
# account, and (if needed) reboots the machine so that we can resize
# its 2 GB virtual disk.  The reboot is needed because GNS3 currently
# (2.2.15) can't resize a disk before starting a node for the first
# time.
#
# I'd also like to set the DHCP client to use a pre-set client
# identifier so that the VM always boots onto the same IP address,
# but cloud-init doesn't seem to have any option to support that.

import sys
import requests
from requests.auth import HTTPBasicAuth
import json
import os
import time
import tempfile
import pprint

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
parser.add_argument('-c', '--cpus', default=1,
                    help='number of virtual CPUs (default 1)')
parser.add_argument('-m', '--memory', default=4096,
                    help='MBs of virtual RAM (default 4096)')
parser.add_argument('-s', '--disk', default=2048,
                    help='MBs of virtual disk (default 2048)')
parser.add_argument('-r', '--release', default=20,
                    help='Ubuntu major release number (default 20)')
parser.add_argument('-q', '--query', action="store_true",
                    help='query the existence of the nodes')
parser.add_argument('-v', '--verbose', action="store_true",
                    help='print the JSON node structure')
args = parser.parse_args()

cloud_image = cloud_images[int(args.release)]

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

url = "http://{}/v2/projects/{}/links".format(gns3_server, project_id)

result = requests.get(url, auth=auth)
result.raise_for_status()

links = result.json()

# Does 'ubuntu' already exist in the project?
#
# GNS3 sometimes appends a number to the node name and creates
# "ubuntu1", so we identify our node as any node whose name
# begins with args.name.

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

# Find switches and find the first unoccupied port on a switch
# (actually only works right now if there's only a single switch)

# We identify switches by looking for the string "switch" in the
# name of the SVG file used for the node's icon.

switches = [n['node_id'] for n in nodes if 'switch' in n['symbol']]

adapters = [a for l in links for a in l['nodes']]
occupied_adapter_numbers = [a['adapter_number'] for a in adapters if a['node_id'] in switches]

# from https://stackoverflow.com/a/28178803
first_unoccupied_adapter = next(i for i, e in enumerate(sorted(occupied_adapter_numbers) + [ None ], 1) if i != e)


# Create an ISO image containing the boot configuration and upload it
# to the GNS3 project.  We write the config to a temporary file,
# convert it to ISO image, then post the ISO image to GNS3.

print("Building cloud-init configuration...")

meta_data = """instance-id: ubuntu
local-hostname: {}
""".format(args.name)

user_data = """#cloud-config
ssh_authorized_keys:
    - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCj6Vc0dUbmLEXByfgwtbG0teq+lhn1ZeCpBp/Ll+yapeTbdP0AuA9iZrcIi4O25ucy+VaZDutj2noNvkcq8dPrCmveX0Zxbylia7rNbd91DPU/94JRidElJPzB5eueObqiVWNWu1cGP0WdaHbecWy0Xu4fq+FqJn3z99Cg4XDYVsfP9avin6McHAaYItTmZHAuHgfL6hJCw4Ju0I7OMAlXgeb9S50nYpzN8ItbRmNQDZC3wdPs5iTd0LgGG/0P7ixhTWDSg5DeQc6JJ2rYezyzc1Lek3lQuBK6FiuvEyd99H2FrowN0b/n1pTQd//pq1G0AcGiwl0ttZ5i2HMe8sab baccala@max
    - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCrP+7mipq2WDogHqJ4So8F4fwPNj87sfOuFh6c3Md5SHg3B3U29Mqu+MgVz9aZ60Nsfr5/blZA7Kjx0GeMHiZHnVf8hS4R8vx066Ck479ZL+6kXDijkxBTPQoTfpuRsqN+vhX5pS+WAPfgKl6pcRtonTMBY1dh/B+KQBhQ2KzdydpDz7dLQRmuKIKNvyNhs4CRS0P8oFZlmuvDjdmvkmKbyp06sZAFHbbWhLs0PHobItNDviwRrBg59tS9Dr40raGUrp3SIsaQTIT56zQAdVB36iZDqYbUf/rCizIcsoCWB76LW7JMvJot1NVKtN9D56ZCgXhW4IJ1dWw2bPY+6lz3 BrentBaccala@max
"""

# A cloud-init runcmd will only run once, at the end of the VM's first boot.
# This one will cause the node to shutdown so we can resize the disk.

if args.disk != 2048:
    user_data.append("""runcmd:
   - [ shutdown, -h, now ]
""")

meta_data_file = tempfile.NamedTemporaryFile(delete = False)
meta_data_file.write(meta_data.encode('utf-8'))
meta_data_file.close()

user_data_file = tempfile.NamedTemporaryFile(delete = False)
user_data_file.write(user_data.encode('utf-8'))
user_data_file.close()

import subprocess

genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                       "-relaxed-filenames", "-V", "cidata", "-graft-points",
                       "meta-data={}".format(meta_data_file.name),
                       "user-data={}".format(user_data_file.name)]

genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE)

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

result = requests.post(url, auth=auth, data=json.dumps(ubuntu_node))
result.raise_for_status()
ubuntu = result.json()

# LINK TO SWITCH

print("Configuring link to switch...")

url = "http://{}/v2/projects/{}/links".format(gns3_server, project_id)

link_obj = {'nodes' : [{'adapter_number' : 0,
                        'port_number' : 0,
                        'node_id' : ubuntu['node_id']},
                       {'adapter_number' : first_unoccupied_adapter,
                        'port_number' : 0,
                        'node_id' : switches[0]}]}

result = requests.post(url, auth=auth, data=json.dumps(link_obj))
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

if args.disk == 2048:
    exit(0)

print("Waiting for node to stop (so we can resize its disk)...")

node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, ubuntu['node_id'])
result = requests.get(node_url, auth=auth)
result.raise_for_status()
while result.json()['status'] == 'started':
    time.sleep(1)
    result = requests.get(node_url, auth=auth)
    result.raise_for_status()

# RESIZE THE DISK

# Doesn't work before you boot.
#
# You currently have to start the VM in order to create a linked clone of the disk image,
# so the disk we're trying to resize doesn't exist until we start the node.

print("Extending disk by {} MB...", args.disk - 2048)

url = "http://{}/v2/compute/projects/{}/qemu/nodes/{}/resize_disk".format(gns3_server, project_id, ubuntu['node_id'])

resize_obj = {'drive_name' : 'hda', 'extend' : args.disk - 2048}

result = requests.post(url, auth=auth, data=json.dumps(resize_obj))
result.raise_for_status()

print("Restarting the node...")
result = requests.post(project_start_url, auth=auth)
result.raise_for_status()