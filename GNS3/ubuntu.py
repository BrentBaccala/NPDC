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
# I'd also like to set the DHCP client to use a pre-set client
# identifier so that the VM always boots onto the same IP address,
# but cloud-init doesn't seem to have any option to support that.

import gns3

import sys
import requests
import yaml
import os
import time
import shutil
import tempfile
import datetime

import argparse

import subprocess

SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', "~/.ssh/authorized_keys"]

# Location of the GNS3 server.  Needed to copy disk files if building a GNS3 appliance.
GNS3_HOME = '/home/gns3'

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

# Parse the command line options

parser = argparse.ArgumentParser(parents=[gns3.parser('ubuntu-test')], description='Start an Ubuntu node in GNS3')

parser.add_argument('-n', '--name', default='ubuntu',
                    help='name of the Ubuntu node (default "ubuntu")')
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
parser.add_argument('--debug', action="store_true",
                    help='allow console login with username ubuntu and password ubuntu')
parser.add_argument('--gns3-appliance', action="store_true",
                    help='build a GNS3 appliance')
parser.add_argument('--boot-script', type=lambda f: open(f), default=None,
                    help="run a script in a screen session after boot")

args = parser.parse_args()

# Open the GNS3 server

gns3_server, gns3_project = gns3.open_project_with_standard_options(args)

# Make sure the cloud image exists on the GNS3 server

cloud_image = cloud_images[args.release]

assert cloud_image in gns3_server.images()

# Does a node with this name already exist in the project?
#
# GNS3 sometimes appends a number to the node name, so we identify our
# node as any node whose name begins with args.name.

if gns3_project.node(args.name):
    print(f"Node {args.name} already exists")
    exit(1)

# Create a GNS3 "cloud" for Internet access.
#
# It's done early in the script like this so that the gns3 library
# knows which interface we're using, because it might need that
# information to construct a notification URL.

cloud = gns3_project.cloud('Internet', args.interface, x=-300, y=0)

switch = gns3_project.switch('InternetSwitch', x=0, y=0)

gns3_project.link(cloud, 0, switch)

notification_url = gns3_project.notification_url()

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

if notification_url:
    user_data['phone_home'] = {'url': notification_url, 'tries' : 1}

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

ubuntu = gns3_project.ubuntu_node(user_data, network_config=network_config, x=300, y=0,
                                  image=cloud_image, ram=args.memory, disk=args.disk, vnc=args.vnc)

gns3_project.link(ubuntu, 0, switch)

# START NODE RUNNING

print("Starting the node...")

# The difference between these two is that start_nodes waits for notification that
# the nodes booted, while start_node does not.
#
# The project might not have a notification_url if the script couldn't figure out
# a local IP address suitable for a callback.

if notification_url:
    gns3_project.start_nodes(ubuntu)
else:
    gns3_project.start_node(ubuntu)

# If you want to connect to the instance and watch its boot script running

if args.boot_script and args.name in gns3_project.httpd.instances_reported:
    ipaddr = gns3_project.httpd.instances_reported[args.name]
    print(f'a cut-and-paste suggestion (to watch the boot script run):   ssh -t ubuntu@{ipaddr} screen -rd')

# Optionally, the script can end by building a gns3 appliance.

if args.gns3_appliance:
    # 1. Add shutdown to the end of the per-once screen script
    # 2. This script waits for shutdown
    print("Waiting for node to shutdown...")
    node_id = ubuntu['node_id']
    project_id = gns3_project.project_id
    while True:
        gns3_project.nodes()  # needed to refresh a cache
        if gns3_project.node(node_id)['status'] == 'stopped':
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
    url = "{}/compute/qemu/images/{}".format(gns3_server.url, cloud_image)
    with tempfile.NamedTemporaryFile() as tmp:
        with requests.get(url, auth=gns3_server.auth, stream=True) as r:
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
