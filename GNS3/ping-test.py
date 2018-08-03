#!/usr/bin/python
#
# Script to fire up a 3-node GNS3 topology, configure basic
# routing between the nodes, and test ping connectivity.

import requests
import json

import math

import subprocess

gns3_server = "blade8:3080"

# Create a new GNS3 project called 'ping-test'
#
# The only required field for a new GNS3 project is 'name'
#
# This will error out with a 409 Conflict if 'ping-test' already exists

print "Creating project..."

new_project = {'name': 'ping-test'}

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

# Configure a cloud node, a switch node, and three CSRv's, each with three interfaces

print "Configuring nodes..."

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

def CSRv_node(num):
    return {
        "compute_id": "local",
        "name": "CiscoCSR1000v16.7.1(a)-{}".format(num),
        "node_type": "qemu",
        "properties": {
            "adapters": 3,
            "hda_disk_image": "csr1000v-universalk9.16.07.01-serial.qcow2",
            "qemu_path": "/usr/bin/qemu-system-x86_64",
            "ram": 3072
        },

        "symbol": ":/symbols/router.svg",
        "x" : int(200 * math.cos((num-1) * math.pi * 2 / 3)),
        "y" : int(200 * math.sin((num-1) * math.pi * 2 / 3)),
    }

CSRv = [None, None, None]

for num in [0,1,2]:
    result = requests.post(url, data=json.dumps(CSRv_node(num)))
    result.raise_for_status()
    CSRv[num] = result.json()

# LINKS

print "Configuring links..."

url = "http://{}/v2/projects/{}/links".format(gns3_server, my_project['project_id'])

# find 'br0' port in cloud object

br0 = [port for port in cloud['ports'] if port['short_name'] == 'br0'][0]

# Link the cloud to the switch

link_obj = {'nodes' : [{'adapter_number' : br0['adapter_number'],
                        'port_number' : br0['port_number'],
                        'node_id' : cloud['node_id']},
                       {'adapter_number' : 0,
                        'port_number' : 0,
                        'node_id' : switch['node_id']}]}

result = requests.post(url, data=json.dumps(link_obj))
result.raise_for_status()

# Link the first interface of each CSRv to the switch

for num in [0,1,2]:
   link_obj = {'nodes' : [{'adapter_number' : 0,
                           'port_number' : 0,
                           'node_id' : CSRv[num]['node_id']},
                          {'adapter_number' : 0,
                           'port_number' : num + 1,
                           'node_id' : switch['node_id']}]}

   result = requests.post(url, data=json.dumps(link_obj))
   result.raise_for_status()

# Link the second and third interfaces of each CSRv together in a ring

for num in [0,1,2]:
   link_obj = {'nodes' : [{'adapter_number' : 1,
                           'port_number' : 0,
                           'node_id' : CSRv[num]['node_id']},
                          {'adapter_number' : 2,
                           'port_number' : 0,
                           'node_id' : CSRv[(num+1)%3]['node_id']}]}

   result = requests.post(url, data=json.dumps(link_obj))
   result.raise_for_status()

# START THE NODES

print "Starting nodes..."

for node in [switch] + CSRv:
   node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, my_project['project_id'], node['node_id'])
   result = requests.post(node_url + "/start")
   result.raise_for_status()

# WAIT FOR NODES TO BOOT
#
# Ideally, we'd like the DHCP server to support notifications of a new client
#
# The ISC Kea server probably can do that, in a somewhat roundabout
# way.  It can use MySQL as a backend, and you can set a MySQL trigger
# for when a new lease appears in the database.  You probably need to
# add an extension to MySQL to let it run shell scripts, which run as
# the MySQL database user, so the script could do something like
# delete the entire database.  Or configure an extension to run the
# scripts as 'nobody'.  Another possibility is to enhance Kea so that
# it directly supports notifications.

print "Waiting for nodes to boot..."

import time

while True:
   time.sleep(10)
