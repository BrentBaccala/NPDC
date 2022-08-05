#!/usr/bin/python3
#
# Script to test a GNS3 GUI client to ensure that it accepts
# cloud-init configuration properly.
#
# RUNTIME DEPENDENCIES
#
# genisoimage must be installed
#
# USAGE
#
# ./ubuntu-test.py IMAGE-NAME
#
# 1. Authentication to GNS3 server
#
#    Provide one of the GNS3_CREDENTIAL_FILES in propfile format;
#    minimal entries are host/port/user/password in the Server block:
#
#    [Server]
#    host = localhost
#    port = 3080
#    user = admin
#    password = password
#
# Can be passed a '-d' option to delete EVERYTHING in an existing project.
#
# The current script installs SSH public key to authenticate me in to
# the "ubuntu" account and POSTs a notification message back to this
# script once the boot process is complete.

import sys
import requests
from requests.auth import HTTPBasicAuth
import json
import yaml
import os
import tempfile
import urllib.parse

import socket
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer

import argparse

import subprocess

import configparser

GNS3_CREDENTIAL_FILES = ["~/gns3_server.conf", "~/.config/GNS3/2.2/gns3_server.conf"]
SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', "~/.ssh/authorized_keys"]

# Which interface on the bare metal system is used to access the Internet from GNS3?
#
# It should be either a routed virtual link to the bare metal system, or
# a bridged interface to a physical network device.

INTERNET_INTERFACE = 'veth1'

# Parse the command line options

parser = argparse.ArgumentParser(description='Start an Ubuntu node in GNS3')
parser.add_argument('-p', '--project', default='BigBlueButton',
                    help='name of the GNS3 project (default "BigBlueButton")')
parser.add_argument('--debug', action="store_true",
                    help='allow console login with username ubuntu and password ubuntu')
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('-d', '--delete', action="store_true",
                    help='delete everything in the project instead of creating it')
group.add_argument('--ls', action="store_true",
                    help='list running nodes')
group.add_argument('client_image', metavar='FILENAME', nargs='?',
                    help='client image to test')
args = parser.parse_args()

# Obtain the credentials needed to authenticate ourself to the GNS3 server

config = configparser.ConfigParser()
for propfilename in GNS3_CREDENTIAL_FILES:
    propfilename = os.path.expanduser(propfilename)
    if os.path.exists(propfilename):
        config.read(propfilename)
        break
try:
    gns3_server = config['Server']['host'] + ":" + config['Server']['port']
except:
    print('No GNS3 server/host/port configuration found')
    exit(1)

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
        project_status = project['status']

if not project_id:
    print("Couldn't find project '{}'".format(args.project))
    exit(1)

print("'{}' is {}".format(args.project, project_id))

# Open the project, if needed

if project_status != 'opened':
    print("Opening project...")

    url = "http://{}/v2/projects/{}/open".format(gns3_server, project_id)

    result = requests.post(url, auth=auth, data=json.dumps({}))
    result.raise_for_status()

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
        #print(node['name'])
        print(json.dumps(node, indent=4))
    exit(0)

if args.delete:
    for node in nodes:
        print("deleting {}...".format(node['name']))
        node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, node['node_id'])
        result = requests.delete(node_url, auth=auth)
        result.raise_for_status()
    exit(0)

# Make sure the cloud image exists on the GNS3 server
#
# GNS3 doesn't seem to support a HEAD method on its images, so we get
# a directory of all of them and search for the one we want

url = "http://{}/v2/compute/qemu/images".format(gns3_server)
if not any (f for f in requests.get(url, auth=auth).json() if f['filename'] == args.client_image):
    print(f"{args.client_image} isn't available on {gns3_server}")
    exit(1)

# Find out if the system we're running on is configured to use an apt proxy.

apt_proxy = None
apt_config_command = ['apt-config', '--format', '%f %v%n', 'dump']
apt_config_proc = subprocess.Popen(apt_config_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
for config_line in apt_config_proc.stdout.read().decode().split('\n'):
    if ' ' in config_line:
        key,value = config_line.split(' ', 1)
        if key == 'Acquire::http::Proxy':
            apt_proxy = value

# Obtain any credentials to authenticate ourself to the VM

ssh_authorized_keys = []
for keyfilename in SSH_AUTHORIZED_KEYS_FILES:
    keyfilename = os.path.expanduser(keyfilename)
    if os.path.exists(keyfilename):
        with open(keyfilename) as f:
            for l in f.read().split('\n'):
                if l.startswith('ssh-'):
                    ssh_authorized_keys.append(l)

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
instance_content = {}
instance_report_cv = threading.Condition()

class RequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = self.headers['Content-Length']
        self.send_response_only(100)
        self.end_headers()

        content = urllib.parse.parse_qs(self.rfile.read(int(length)))
        hostname = content[b'hostname'][0]

        with instance_report_cv:
            if not hostname in instances_reported:
                instances_reported.add(hostname)
                instance_content[hostname] = content
                instance_report_cv.notify()

        self.send_response(200)
        self.end_headers()

server_address = ('', 0)
httpd = HTTPServer(server_address, RequestHandler)
threading.Thread(target=httpd.serve_forever).start()
notification_url = "http://{}:{}/".format(script_ip, httpd.server_port)

### FUNCTIONS TO CREATE VARIOUS KINDS OF GNS3 OBJECTS

def create_ubuntu_node(user_data, x=0, y=0, image=None, cpus=None, ram=None, disk=None, ethernets=None, vnc=None):
    r"""create_ubuntu_node(user_data, x=0, y=0, cpus=None, ram=None, disk=None)
    ram and disk are both in MB; ram defaults to 256 MB; disk defaults to 2 GB
    """
    # Create an ISO image containing the boot configuration and upload it
    # to the GNS3 project.  We write the config to a temporary file,
    # convert it to ISO image, then post the ISO image to GNS3.

    print(f"Building cloud-init configuration for {user_data['hostname']}...")

    # The 'meta-data' file must be present for the ISO image to be recognized
    # as a valid cloud-init configuration source, but the NoCloud data source
    # merges meta-data with existing meta-data, while user-data and
    # network-config overwrite existing configuration.  So we can just specify
    # an empty dictionary here to keep our existing meta-data, which is just
    # going to be the system UUID as instance-id, as set by the systemd
    # service installed in ubuntu.py.

    meta_data = dict()

    # Generate the ISO image that will be used as a virtual CD-ROM to pass all this initialization data to cloud-init.

    meta_data_file = tempfile.NamedTemporaryFile(delete = False)
    meta_data_file.write(yaml.dump(meta_data).encode('utf-8'))
    meta_data_file.close()

    user_data_file = tempfile.NamedTemporaryFile(delete = False)
    user_data_file.write(("#cloud-config\n" + yaml.dump(user_data)).encode('utf-8'))
    user_data_file.close()

    network_config_file = tempfile.NamedTemporaryFile(delete = False)
    network_config_file.write(yaml.dump(user_data['network']).encode('utf-8'))
    network_config_file.close()

    genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                           "-relaxed-filenames", "-V", "cidata", "-graft-points",
                           "meta-data={}".format(meta_data_file.name),
                           "network-config={}".format(network_config_file.name),
                           "user-data={}".format(user_data_file.name)]

    genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    isoimage = genisoimage_proc.stdout.read()

    debug_isoimage = False
    if debug_isoimage:
        with open('isoimage-debug.iso', 'wb') as f:
            f.write(isoimage)

    os.remove(meta_data_file.name)
    os.remove(user_data_file.name)
    os.remove(network_config_file.name)

    print(f"Uploading cloud-init configuration for {user_data['hostname']}...")

    # files in the GNS3 directory take precedence over these project files,
    # so we need to make these file names unique
    cdrom_image = project_id + '_' + user_data['hostname'] + '.iso'
    file_url = "http://{}/v2/projects/{}/files/{}".format(gns3_server, project_id, cdrom_image)
    result = requests.post(file_url, auth=auth, data=isoimage)
    result.raise_for_status()

    # Configure an Ubuntu cloud node

    print(f"Configuring {user_data['hostname']} node...")

    url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

    # It's important to use the scsi disk interface, because the IDE interface in qemu
    # has some kind of bug, probably in its handling of DISCARD operations, that
    # causes a thin provisioned disk to balloon up with garbage.
    #
    # See https://unix.stackexchange.com/questions/700050
    # and https://bugs.launchpad.net/ubuntu/+source/qemu/+bug/1974100

    ubuntu_node = {
            "compute_id": "local",
            "name": user_data['hostname'],
            "node_type": "qemu",
            "properties": {
                "adapter_type" : "virtio-net-pci",
                "hda_disk_image": image,
                "hda_disk_interface": "scsi",
                "cdrom_image" : cdrom_image,
                "qemu_path": "/usr/bin/qemu-system-x86_64",
            },

            "symbol": ":/symbols/qemu_guest.svg",
            "x" : x,
            "y" : y
        }

    if cpus:
        ubuntu_node['properties']['cpus'] = cpus
    if ram:
        ubuntu_node['properties']['ram'] = ram
    if ethernets:
        ubuntu_node['properties']['adapters'] = ethernets
    if vnc:
        ubuntu_node['console_type'] = 'vnc'

    result = requests.post(url, auth=auth, data=json.dumps(ubuntu_node))
    result.raise_for_status()
    ubuntu = result.json()

    if disk and disk > 2048:
        url = "http://{}/v2/compute/projects/{}/qemu/nodes/{}/resize_disk".format(gns3_server, project_id, ubuntu['node_id'])
        resize_obj = {'drive_name' : 'hda', 'extend' : disk - 2048}
        result = requests.post(url, auth=auth, data=json.dumps(resize_obj))
        result.raise_for_status()

    return ubuntu

def start_ubuntu_node(ubuntu):

    print(f"Starting the {ubuntu['name']} node...")

    project_start_url = "http://{}/v2/projects/{}/nodes/{}/start".format(gns3_server, project_id, ubuntu['node_id'])
    result = requests.post(project_start_url, auth=auth)
    result.raise_for_status()

def create_cloud(name, interface, x=0, y=0):

    print(f"Configuring cloud {name} for access to interface {interface}...")

    cloud_node = {
            "compute_id": "local",
            "name": name,
            "node_type": "cloud",

            "properties" : {
            "ports_mapping": [
                {
                    "interface": interface,
                    "name": interface,
                    "port_number": 0,
                    "type": "ethernet"
                }
            ],
            },

            "symbol": ":/symbols/cloud.svg",
            "x" : x,
            "y" : y,
        }

    url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

    result = requests.post(url, auth=auth, data=json.dumps(cloud_node))
    result.raise_for_status()
    return result.json()

def create_switch(name, x=0, y=0):

    print(f"Configuring Ethernet switch {name}...")

    switch_node = {
        "compute_id": "local",
        "name": name,
        "node_type": "ethernet_switch",

        "symbol": ":/symbols/ethernet_switch.svg",
        "x" : x,
        "y" : y
    }

    url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

    result = requests.post(url, auth=auth, data=json.dumps(switch_node))
    result.raise_for_status()
    return result.json()

def create_link(node1, port1, node2, port2):
    url = "http://{}/v2/projects/{}/links".format(gns3_server, project_id)

    link_obj = {'nodes' : [{'adapter_number' : node1['ports'][port1]['adapter_number'],
                            'port_number' : node1['ports'][port1]['port_number'],
                            'node_id' : node1['node_id']},
                           {'adapter_number' : node2['ports'][port2]['adapter_number'],
                            'port_number' : node2['ports'][port2]['port_number'],
                            'node_id' : node2['node_id']}]}

    result = requests.post(url, auth=auth, data=json.dumps(link_obj))
    result.raise_for_status()

# CREATE A NAT GATEWAY BETWEEN OUR PUBLIC INTERNET AND THE ACTUAL INTERNET

user_data = {'hostname': 'NAT1',
             'network': {'version': 2, 'ethernets': {'ens4': {'dhcp4': 'on', 'dhcp-identifier': 'mac'},
                                                     'ens5': {'addresses': ['128.8.8.254/24'], 'optional' : True}}},
             'packages': ['dnsmasq'],
             'package_upgrade': True,
             'phone_home': {'url': notification_url, 'tries': 1},
             'runcmd': ['iptables -t nat -A POSTROUTING -o ens4 -j MASQUERADE',
                        'sysctl net.ipv4.ip_forward=1',
                        'echo listen-address=128.8.8.254 >> /etc/dnsmasq.conf',
                        'echo bind-interfaces >> /etc/dnsmasq.conf',
                        'echo dhcp-range=128.8.8.1,128.8.8.100,12h >> /etc/dnsmasq.conf',
                        'echo dhcp-sequential-ip >> /etc/dnsmasq.conf',
                        'echo dhcp-authoritative >> /etc/dnsmasq.conf',
# commented out since we don't configure ens5, and therefore this will fail and cause cloud-init to report failure
#                        'systemctl start dnsmasq'
                       ],
}

if args.debug:
    user_data['users'] = [{'name': 'ubuntu',
                           'plain_text_passwd': 'ubuntu',
                           'ssh_authorized_keys': ssh_authorized_keys,
                           'lock_passwd': False,
                           'shell': '/bin/bash',
                           'sudo': 'ALL=(ALL) NOPASSWD:ALL',
    }]

# If the system we're running on is configured to use an apt proxy, use it for the NAT1 instance as well.
#
# This will break things if the instance can't reach the proxy, so I only use it for NAT1.

if apt_proxy:
    user_data['apt'] = {'http_proxy': apt_proxy}

nat1 = create_ubuntu_node(user_data, image=args.client_image, ram=8192, ethernets=2, vnc=True)

cloud = create_cloud('Internet', INTERNET_INTERFACE, x=-400, y=0)

create_link(nat1, 0, cloud, 0)

start_ubuntu_node(nat1)

# reports ubuntu

print("Waiting for NAT1 to boot...")
with instance_report_cv:
    while b'NAT1' not in instances_reported:
        instance_report_cv.wait()
httpd.shutdown()
