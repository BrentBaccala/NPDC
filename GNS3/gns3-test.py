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

import gns3

import os
import json
import argparse
import subprocess

SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', "~/.ssh/authorized_keys"]

# Which interface on the bare metal system is used to access the Internet from GNS3?
#
# It should be either a routed virtual link to the bare metal system, or
# a bridged interface to a physical network device.

INTERNET_INTERFACE = 'veth'

# Parse the command line options

parser = argparse.ArgumentParser(description='Start an Ubuntu node in GNS3')
parser.add_argument('-H', '--host',
                    help='name of the GNS3 host')
parser.add_argument('-p', '--project', default='BigBlueButton',
                    help='name of the GNS3 project (default "BigBlueButton")')
parser.add_argument('--debug', action="store_true",
                    help='allow console login with username ubuntu and password ubuntu')
group = parser.add_mutually_exclusive_group()
group.add_argument('--delete-everything', action="store_true",
                    help='delete everything in the project instead of creating it')
group.add_argument('--delete', type=str,
                    help='delete everything in the project matching a substring')
group.add_argument('--ls', action="store_true",
                    help='list running nodes')
group.add_argument('--ls-images', action="store_true",
                    help='list running nodes')
group.add_argument('--ls-all', action="store_true",
                    help='list running nodes')
group.add_argument('client_image', metavar='FILENAME', nargs='?',
                    help='client image to test')
args = parser.parse_args()

# Open the GNS3 server

gns3_server = gns3.Server(host=args.host)

if args.ls_images:
    print(gns3_server.images())
    exit(0)

# Find the GNS3 project called project_name

print("Finding project", args.project)

gns3_project = gns3_server.project(args.project)

gns3_project.open()

if args.ls:
    print([n['name'] for n in gns3_project.nodes()])
    exit(0)

if args.ls_all:
    print(json.dumps(gns3_project.nodes(), indent=4))
    print(json.dumps(gns3_project.links(), indent=4))
    exit(0)

if args.delete_everything:
    gns3_project.delete_everything()
    exit(0)

if args.delete:
    gns3_project.delete_substring(args.delete)
    exit(0)

# Make sure the cloud image exists on the GNS3 server
#
# GNS3 doesn't seem to support a HEAD method on its images, so we get
# a directory of all of them and search for the one we want

if args.client_image:
    assert args.client_image in gns3_server.images()
else:
    args.client_image = next(image for image in gns3_server.images() if image.startswith('ubuntu'))

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

user_data = {'hostname': 'ubuntu',
             'network': {'version': 2, 'ethernets': {'ens4': {'dhcp4': 'on', 'dhcp-identifier': 'mac'},
                                                     'ens5': {'addresses': ['128.8.8.254/24'], 'optional' : True}}},
             'package_upgrade': True,
#             'phone_home': {'url': notification_url, 'tries': 1},
}

user_data = {'hostname': 'ubuntu'}

if args.debug:
    user_data['users'] = [{'name': 'ubuntu',
                           'plain_text_passwd': 'ubuntu',
                           'ssh_authorized_keys': ssh_authorized_keys,
                           'lock_passwd': False,
                           'shell': '/bin/bash',
                           'sudo': 'ALL=(ALL) NOPASSWD:ALL',
    }]

# If the system we're running on is configured to use an apt proxy, use it for the ubuntu instance as well.
#
# This will break things if the instance can't reach the proxy, so I only use it for ubuntu.

#if apt_proxy:
#    user_data['apt'] = {'http_proxy': apt_proxy}

switch = gns3_project.switch('InternetSwitch', x=0, y=0)

ubuntu = gns3_project.ubuntu_node(user_data, image=args.client_image, x=200, y=200)

cloud = gns3_project.cloud('Internet', INTERNET_INTERFACE, x=-200, y=200)

gns3_project.link(ubuntu, 0, switch)
gns3_project.link(cloud, 0, switch)

gns3_project.start_ubuntu_node(ubuntu)
