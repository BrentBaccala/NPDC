#!/usr/bin/python
#
# Script to sort GNS3 routers by IP address, arrange them in a circle
# around their switch, pair them up, and set up links between each
# pair.

import requests
import json

import math

import subprocess

# Get this blade's arp table to map MAC addresses (the only thing
# provided by GNS3) to IP addresses.  Since the blade is acting as the
# DHCP server for all of the routers, it should have ARP entries
# for all of them.

arp_proc = subprocess.Popen(["arp"], stdout=subprocess.PIPE)

arp_table = {}
for line in arp_proc.stdout:
    fields = line.split()
    #print fields[0], fields[2]
    arp_table[fields[2]] = fields[0]

# A hard-coded URL pointing to the GNS3 REST interface for this project.

url = "http://localhost:3080/v2/projects/6bbf314e-4858-4345-978e-51ac64010c24"

# Get a list of all the nodes

nodes_response = requests.get(url + "/nodes")
nodes_response.raise_for_status()

nodes = json.loads(nodes_response.text)

# Make a table mapping IP addresses to node IDs.

nodes_by_ipaddr = {}

for n in nodes:
    if 'CiscoCSR1000v' in n['name'] or '192.168.57' in n['name']:
    #if n['command_line'] != None and 'CiscoCSR1000v' in n['command_line']:
        ip_address = arp_table[n['properties']['mac_address']]
        nodes_by_ipaddr[ip_address] = n['node_id']
        print n['node_id'], n['properties']['mac_address'], ip_address

# Reposition the nodes in a circle around the switch
# in the center, and rename them to be their IP address.

def reposition_nodes():
    for n in nodes:
        if 'switch' in n['name']:
            put_obj = {'x': 0, 'y' : 0}
        if 'Cloud' in n['name']:
            put_obj = {'x': -400, 'y' : 0}
        if 'CiscoCSR1000v' in n['name'] or '192.168.57' in n['name']:

            # There's one node (the switch) in the center, and all the other nodes (len(nodes)-1 of
            # them) are positioned equidistantly around the switch in a circle, sorted by IP
            # address.  I substract "99" and not "100" from the IP address to start the routers
            # numbering at one, since slot zero is taken by "Cloud".

            ip_address = arp_table[n['properties']['mac_address']]

            index = float(ip_address.split('.')[3]) - 99
            angle = 2 * math.pi * index/(len(nodes)-1)
            x = -int(300 * math.cos(angle))
            y = -int(300 * math.sin(angle))

            label = n['label']
            label['x'] = -int(50 * math.cos(angle)) - 25
            label['y'] = -int(50 * math.sin(angle))

            put_obj = {'x': x, 'y' : y, 'name' : ip_address, 'label' : label}

        result = requests.put(url + "/nodes/" + n['node_id'], data=json.dumps(put_obj))
        result.raise_for_status()
        print result.text

# For each pair of routers, create a link between their second adapters.

def link_pairs():
    for i in range(100,116,2):
        nodeA = nodes_by_ipaddr['192.168.57.' + str(i)]
        nodeB = nodes_by_ipaddr['192.168.57.' + str(i+1)]
        link_obj = {'nodes' : [{'adapter_number' : 1, 'port_number' : 0, 'node_id' : id} for id in [nodeA, nodeB]]}
        result = requests.post(url + "/links", data=json.dumps(link_obj))
        result.raise_for_status()
        print result.text

# For demonstration purposes, remove the paired links, relying on the
# fact that only the links between pairs have adapter number one on
# both sides of the link.

def unlink_pairs():
    links_response = requests.get(url + "/links")
    links_response.raise_for_status()
    links = json.loads(links_response.text)

    for l in links:
        if [n['adapter_number'] for n in l['nodes']] == [1,1]:
            print l['link_id']
            result = requests.delete(url + "/links/" + l['link_id'])
            result.raise_for_status()
            print result.text

def print_links():
    links_response = requests.get(url + "/links")
    links_response.raise_for_status()
    links = json.loads(links_response.text)

    for l in links:
        print [(n['adapter_number'], n['port_number']) for n in l['nodes']]

def erase_link_names():
    links_response = requests.get(url + "/links")
    links_response.raise_for_status()
    links = json.loads(links_response.text)

    for l in links:
        #if [n['adapter_number'] for n in l['nodes']] == [1,1]:
            print l['link_id']
            if l['nodes'][0]['label']['text'] != 'br0':
                l['nodes'][0]['label']['text'] = ''
            if l['nodes'][1]['label']['text'] != 'br0':
                l['nodes'][1]['label']['text'] = ''
            result = requests.put(url + "/links/" + l['link_id'], data=json.dumps(l))
            result.raise_for_status()
            print result.text
