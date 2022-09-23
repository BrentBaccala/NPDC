#!/usr/bin/python3
#
# Script to start a GNS3 Ubuntu virtual machine named "ubuntu" on
# the existing project "Virtual Network".  These names can be changed
# with command line options, with also let the user select the Ubuntu
# release, the virtual memory size, the virtual disk size, and the
# number of CPUs.
#
# It will be configured to accept your ssh keys for ssh access,
# and a GNU screen session will start as 'ubuntu' running a
# specified boot script.
#
# It will give you a cut-and-paste suggestion to ssh into the VM and watch its boot script run.
# If you request a GNS3 appliance, it will also shutdown the VM after the boot script finishes
# and build a GNS3 appliance.  THIS REQUIRES READ ACCESS TO THE GNS3 SERVER'S DIRECTORY.
#
# RUNTIME DEPENDENCIES
#
# genisoimage must be installed
#
# USAGE
#
# ./ubuntu.py -n ubuntu -r 18 -s $((1024*1024)) --vnc --boot-script opendesktop.sh --gns3-appliance
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
import shutil
import tempfile
import pprint
import urllib.parse
import datetime
import hashlib

import socket
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer

import argparse

import subprocess

import configparser

GNS3_CREDENTIAL_FILES = ["~/gns3_server.conf", "~/.config/GNS3/2.2/gns3_server.conf"]
SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', "~/.ssh/authorized_keys"]

# Location of the GNS3 server.  Needed to copy disk files if building a GNS3 appliance.
GNS3_HOME = '/home/gns3'

GNS3_APPLIANCE_FILE = 'opendesktop.gns3a'

# These are bootable images provided by Canonical, Inc, that have the cloud-init package
# installed.  When booted in a VM, cloud-init will configure them based on configuration
# provided (in our case) on a ISO image attached to a virtual CD-ROM device.
#
# Pick up the latest versions from here:
#
# https://cloud-images.ubuntu.com/releases/bionic/release/ubuntu-18.04-server-cloudimg-amd64.img
# https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-amd64.img
# https://cloud-images.ubuntu.com/releases/jammy/release/ubuntu-22.04-server-cloudimg-amd64.img
#
# Updated versions are released several times a month.  If you don't have the latest version,
# don't worry, this file's cloud-init configuration will run a package update, but once the GNS3
# appliance is built, it's not going to run again.  Just connect the new VM to the Internet and
# run a package update.

cloud_images = {
    22: 'ubuntu-22.04-server-cloudimg-amd64.img',
    20: 'ubuntu-20.04-server-cloudimg-amd64.img',
    18: 'ubuntu-18.04-server-cloudimg-amd64.img'
}

# Utility function used when generating a GNS3 appliance

def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

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
parser.add_argument('--gns3-appliance', action="store_true",
                    help='build a GNS3 appliance')
parser.add_argument('--boot-script', type=lambda f: open(f), default=None,
                    help="run a script in a screen session after boot")
parser.add_argument('-q', '--query', action="store_true",
                    help='query the existence of the nodes')
parser.add_argument('-v', '--verbose', action="store_true",
                    help='print the JSON node structure')
parser.add_argument('--debug', action="store_true",
                    help='allow console login with username ubuntu and password ubuntu')
args = parser.parse_args()

cloud_image = cloud_images[args.release]

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

# Make sure the cloud image exists on the GNS3 server
#
# GNS3 doesn't seem to support a HEAD method on its images, so we get
# a directory of all of them and search for the one we want

url = "http://{}/v2/compute/qemu/images".format(gns3_server)
if not any (f for f in requests.get(url, auth=auth).json() if f['filename'] == cloud_image):
    print(f"{cloud_image} isn't available on {gns3_server}")
    exit(1)

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
    print("Couldn't find GNS3 project '{}'".format(args.project))
    exit(1)

print("'{}' is {}".format(args.project, project_id))

# Open the project, if needed

if project_status != 'opened':
    print("Opening project...")

    url = "http://{}/v2/projects/{}/open".format(gns3_server, project_id)

    result = requests.post(url, auth=auth, data=json.dumps({}))
    result.raise_for_status()

# Find the available project files (doesn't seem to work)

#url = "http://{}/v2/projects/{}/files".format(gns3_server, project_id)
#url = "http://{}/v2/qemu/images".format(gns3_server)

#result = requests.get(url, auth=auth)
#result.raise_for_status()
#print(result.json())

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

ubuntus = [n['node_id'] for n in nodes if n['name'] == args.name]

if len(ubuntus) > 0:
    print("{} already exists as node {}".format(args.name, ubuntus[0]))
    node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, ubuntus[0])
    if args.verbose:
        pprint.pprint(next(n for n in nodes if n['name'].startswith(args.name)))
    if args.delete:
        print("deleting {}...".format(ubuntus[0]))
        result = requests.delete(node_url, auth=auth)
        result.raise_for_status()
        exit(0)
    if not args.gns3_appliance:
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

if len(switches) == 0:
    print("No virtual switches configured; configure one and provide it with Internet access")
    exit(1)

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
instance_content = {}
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
                instance_content[self.client_address[0]] = urllib.parse.parse_qs(content)
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

# Find out if the system we're running on is configured to use an apt proxy.

apt_proxy = None
apt_config_command = ['apt-config', '--format', '%f %v%n', 'dump']
apt_config_proc = subprocess.Popen(apt_config_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
for config_line in apt_config_proc.stdout.read().decode().split('\n'):
    if ' ' in config_line:
        key,value = config_line.split(' ', 1)
        if key == 'Acquire::http::Proxy':
            apt_proxy = value

# Create an ISO image containing the boot configuration and upload it
# to the GNS3 project.  We write the config to a temporary file,
# convert it to ISO image, then post the ISO image to GNS3.

print("Building cloud-init configuration...")

meta_data = {'instance-id' : 'ubuntu'}

# Obtain any credentials to authenticate ourself to the VM

ssh_authorized_keys = []
for keyfilename in SSH_AUTHORIZED_KEYS_FILES:
    keyfilename = os.path.expanduser(keyfilename)
    if os.path.exists(keyfilename):
        with open(keyfilename) as f:
            for l in f.read().split('\n'):
                if l.startswith('ssh-'):
                    ssh_authorized_keys.append(l)

if args.boot_script:
    boot_script = args.boot_script.read()
    if args.gns3_appliance:
        # If we're building an appliance, shutdown the system for cloning after the script is done
        boot_script += "\nsudo shutdown -h now\n"
    else:
        # If we're not building an appliance, exec bash to keep the 'screen' session running
        boot_script += "\nexec bash\n"

# use the host's apt proxy (if any) for the boot script
if apt_proxy:
    proxy_environment_setting = f'http_proxy="{apt_proxy}"'
else:
    proxy_environment_setting = ''

# Still need to run 'systemctl enable assign_cloudinit_instanceid.service' in runcmd

systemd_service = f"""[Unit]
Description=Assign system-uuid as cloud-init instance-id
DefaultDependencies=no
After=systemd-remount-fs.service
Before=cloud-init-local.service

[Install]
WantedBy=cloud-init.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=bash -c "echo instance-id: $(dmidecode -s system-uuid) > /var/lib/cloud/seed/nocloud/meta-data"
"""

# default is use a disk file as the dhcp-identifier, which causes all cloned
# images to use the same dhcp-identifier and get the same IP address,
# so configure the dhcp-identifier to be the instance's MAC address
#
# Currently need NetworkManager to recognize ipv6-address-generation
#
# Use the instance's MAC address to identify itself to dhcp, not the
# hostname, which will probably be 'ubuntu', and use RFC 7217 to
# generate IPv6 addresses, because web browsers are starting to filter
# out the older eui64 RFC 4291 addresses.
#
# Commented out ipv6-address-generation because it requires renderer: NetworkManager

network_config = {'version': 2,
                  'ethernets':
                  {'ens4': {'dhcp4': 'on',
                            'dhcp-identifier': 'mac',
#                            'ipv6-address-generation': 'stable-privacy',
                  }}}

user_data = {'hostname': args.name,
             # don't do package_upgrade, because it delays phone_home until it's done,
             # so I've put an 'apt upgrade' at the beginning of the opendesktop.sh script
             # 'package_upgrade': True,
             'ssh_authorized_keys': ssh_authorized_keys,
             'phone_home': {'url': notification_url},
             'write_files' : [
                 {'path': '/lib/systemd/system/assign_cloudinit_instanceid.service',
                  'permissions': '0644',
                  'content': systemd_service
                 },
                 # user-data and meta-data have to be present for cloud-init to identify the
                 # NoCloud data source as present, and this check is made quite early during boot,
                 # in particular before systemd unit files (like assign_cloudinit_instanceid) are run
                 {'path': '/var/lib/cloud/seed/nocloud/user-data',
                  'permissions': '0644',
                  'content': ''
                 },
                 {'path': '/var/lib/cloud/seed/nocloud/meta-data',
                  'permissions': '0644',
                  'content': ''
                 },
                 {'path': '/var/lib/cloud/seed/nocloud/network-config',
                  'permissions': '0644',
                  'content': yaml.dump(network_config)
                 },
             ],
             'runcmd' : ['systemctl enable assign_cloudinit_instanceid.service']
}

if args.debug:
    user_data['users'] = [{'name': 'ubuntu',
                           'plain_text_passwd': 'ubuntu',
                           'ssh_authorized_keys': ssh_authorized_keys,
                           'lock_passwd': False,
                           'shell': '/bin/bash',
                           'sudo': 'ALL=(ALL) NOPASSWD:ALL',
    }]

# Putting files in /home/ubuntu cause that directory's permissions to change to root.root,
# probably because it's being created too early in the boot process.  Put files in / to avoid this.
# I remove these files at the end of opendesktop.sh.

if args.boot_script:
    user_data['write_files'].append({'path': '/boot.sh',
                                     'permissions': '0755',
                                     'content': boot_script
    })
    user_data['runcmd'].append(f'su ubuntu -c "{proxy_environment_setting} screen -dm bash -c /boot.sh"')

# If the system we're running on is configured to use an apt proxy, use it for the GNS3 instance as well.
#
# This will break things if the GNS3 instance can't reach the proxy, so I use it for the initial
# installation, but don't set it as the default for later cloned instances.

if apt_proxy:
    # this proxy is used by cloud-init
    user_data['apt'] = {'http_proxy': apt_proxy}
    # this proxy would be used for later cloned instances
    # user_data['write_files'].append({'path': '/etc/apt/apt.conf.d/90proxy',
    #                                  'permissions': '0644',
    #                                  'content': f'Acquire::http::Proxy "{value}"\n'
    #                                 })

# Generate the ISO image that will be used as a virtual CD-ROM to pass all this initialization data to cloud-init.

meta_data_file = tempfile.NamedTemporaryFile(delete = False)
meta_data_file.write(yaml.dump(meta_data).encode('utf-8'))
meta_data_file.close()

user_data_file = tempfile.NamedTemporaryFile(delete = False)
user_data_file.write(("#cloud-config\n" + yaml.dump(user_data)).encode('utf-8'))
user_data_file.close()

network_config_file = tempfile.NamedTemporaryFile(delete = False)
network_config_file.write(yaml.dump(network_config).encode('utf-8'))
network_config_file.close()

genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                       "-relaxed-filenames", "-V", "cidata", "-graft-points",
                       "meta-data={}".format(meta_data_file.name),
                       "user-data={}".format(user_data_file.name),
                       "network-config={}".format(network_config_file.name)]

genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

isoimage = genisoimage_proc.stdout.read()

debug_isoimage = False
if debug_isoimage:
    with open('isoimage-debug.iso', 'wb') as f:
        f.write(isoimage)

os.remove(meta_data_file.name)
os.remove(user_data_file.name)
os.remove(network_config_file.name)

print("Uploading cloud-init configuration...")

# files in the GNS3 directory take precedence over these project files,
# so we need to make these file names unique
cdrom_image = project_id + '_' + args.name + '.iso'
file_url = "http://{}/v2/projects/{}/files/{}".format(gns3_server, project_id, cdrom_image)
result = requests.post(file_url, auth=auth, data=isoimage)
result.raise_for_status()

# Configure an Ubuntu cloud node

print("Configuring Ubuntu cloud node...")

url = "http://{}/v2/projects/{}/nodes".format(gns3_server, project_id)

# It's important to use the scsi disk interface, because the IDE interface in qemu
# has some kind of bug, probably in its handling of DISCARD operations, that
# causes a thin provisioned disk to balloon up with garbage.
#
# See https://unix.stackexchange.com/questions/700050
# and https://bugs.launchpad.net/ubuntu/+source/qemu/+bug/1974100

ubuntu_node = {
        "compute_id": "local",
        "name": args.name,
        "node_type": "qemu",
        "properties": {
            "adapters": 1,
            "adapter_type" : "virtio-net-pci",
            "hda_disk_image": cloud_image,
            "hda_disk_interface": "scsi",
            "cdrom_image" : cdrom_image,
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
                        'label' : {'text' : 'ens3'},
                        'node_id' : ubuntu['node_id']},
                       {'adapter_number' : first_unoccupied_port[0],
                        'port_number' : first_unoccupied_port[1],
                        'label' : {'text' : f'e{first_unoccupied_port[1]}'},
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
# print(instance_content)

httpd.shutdown()

# If you want to now auto-connect to the instance and watch its screen.sh script running

ipaddr=list(instances_reported)[0]
print(f'a cut-and-paste suggestion:   ssh -t ubuntu@{ipaddr} screen -rd')

# You'll still need to ssh in with '-X', not to a screen session, to run Puppeteer tests

# Optionally, the script can end by building a gns3 appliance.

if len(ubuntus) > 0:
    node_id = ubuntus[0]
else:
    node_id = ubuntu['node_id']

if args.gns3_appliance:
    # 1. Add shutdown to the end of the per-once screen script
    # 2. This script waits for shutdown
    print("Waiting for node to shutdown...")
    while True:
        result = requests.get(node_url, auth=auth)
        result.raise_for_status()
        if result.json()['status'] == 'stopped':
            break
        time.sleep(10)

    # 3. Keeps the node UUID
    # 4. NEEDS NO SPECIAL FS PERMISSION IF RUN ON THE GNS3SERVER, SINCE GNS3 LEAVES FILES WORLD-READABLE BY DEFAULT
    # 5. Copies disk UUID file from project uuid directory to pwd
    print("Copying and rebasing disk image...")
    disk_UUID_filename = os.path.join(GNS3_HOME, 'GNS3/projects/{}/project-files/qemu/{}/hda_disk.qcow2'.format(project_id, node_id))
    now = datetime.datetime.now()
    appliance_image_filename = now.strftime('ubuntu-open-desktop-%Y-%h-%d-%H%M.qcow2')
    subprocess.run(['cp', disk_UUID_filename, appliance_image_filename]).check_returncode()
    # 6a. get a copy of the backing image and rebase it
    #     from https://stackoverflow.com/a/39217788/1493790
    url = "http://{}/v2/compute/qemu/images/{}".format(gns3_server, cloud_image)
    with tempfile.NamedTemporaryFile() as tmp:
        with requests.get(url, auth=auth, stream=True) as r:
            shutil.copyfileobj(r.raw, tmp)
        subprocess.run(['qemu-img', 'rebase', '-u', '-b', tmp.name, appliance_image_filename]).check_returncode()
        subprocess.run(['qemu-img', 'rebase', '-b', "", appliance_image_filename]).check_returncode()
    # 6b. rebase the image (need read permission on backing file)
    #    Can you skip this step?  Yes, but rebased file is just less than 1 GB bigger than the original, so that's all you save.
    #    Plus, if you skip this, you have a file that can only be used on the same system, or one with an idential backing file.
    #    Remember that those cloudimg files (the backing file) are updated by Canoncial every few days.
    # subprocess.run(['qemu-img', 'rebase', '-b', "", appliance_image_filename]).check_returncode()
    print("New appliance image created:", appliance_image_filename)
    # Final steps not done by this script:
    # 7. delete the VM (default name 'ubuntu') used by this script
    # 8. add-appliance.py (add the newly created image to the appliance file)
    # 9. upload-image.py (copy the new image to the GNS3 server)
    # 10. import the appliance in the GNS3 GUI
