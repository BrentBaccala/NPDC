#!/usr/bin/python3
#
# Script to start a GNS3 Ubuntu machine named "sagetest"
# on the existing project "Virtual Network".


import requests
from requests.auth import HTTPBasicAuth
import json
import os

import subprocess

import configparser

PROP_FILE = os.path.expanduser("~/.config/GNS3/2.2/gns3_server.conf")

project_name = "Virtual Network"

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
    if project['name'] == project_name:
        project_id = project['project_id']

if not project_id:
    print("Couldn't find project '{}'".format(project_name))
    exit(1)

print("'{}' is {}".format(project_name, project_id))

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
#
# This approach doesn't work with the standard GNS3 implementation,
# which doesn't allow images to be loaded from the project directory.
#
# Requires a patched gns3-server.

print("Building cloud-init configuration...")

meta_data = """
instance-id: ubuntu
local-hostname: ubuntu
"""

user_data = """
#cloud-config
password: ubuntu
chpasswd: { expire: False }
ssh_pwauth: True
"""

import os
import tempfile

meta_data_file = tempfile.NamedTemporaryFile(delete = False)
meta_data_file.write(meta_data.encode('utf-8'))
meta_data_file.close()

user_data_file = tempfile.NamedTemporaryFile(delete = False)
user_data_file.write(user_data.encode('utf-8'))
user_data_file.close()

import subprocess

genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                       "-relaxed-filenames", "-graft-points",
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
        "name": "sagetest",
        "node_type": "qemu",
        "properties": {
            "adapters": 1,
            "adapter_type" : "virtio-net-pci",
            "hda_disk_image": "ubuntu-20.04-server-cloudimg-amd64.img",
            "cdrom_image" : "config.iso",
            "qemu_path": "/usr/bin/qemu-system-x86_64",
            "ram": 4096
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
