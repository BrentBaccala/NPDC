#!/usr/bin/python3
#
# Script to start a GNS3 Ubuntu virtual machine named "sagetest" on
# the existing project "Virtual Network".
#
# Can be passed a '-d' option to delete an existing "sagetest" VM.
#
# We use an Ubuntu cloud image that comes with the cloud-init package
# pre-installed, so that we can construct a configuration script and
# provide it to the VM on a virtual CD-ROM.
#
# The current script installs a pre-generated host key to identify the
# VM, installs an SSH public key to authenticate me in to the "ubuntu"
# account, and reboots the machine so that we can resize its 2 GB
# virtual disk.  The reboot is needed because GNS3 currently (2.2.15)
# can't resize a disk before starting a node for the first time.
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

# Does 'sagetest' already exist in the project?
#
# GNS3 sometimes appends a number to the node name and creates
# "sagetest1", so we identify a "sagetest" node as any node
# whose name begins with "sagetest".

sagetests = [n['node_id'] for n in nodes if n['name'].startswith('sagetest')]

if len(sagetests) > 0:
    print("sagetest already exists as node", sagetests[0])
    if len(sys.argv) > 1 and sys.argv[1] == '-d':
        print("deleting sagetest...")
        node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, sagetests[0])
        result = requests.delete(node_url, auth=auth)
        result.raise_for_status()
        exit(0)
    exit(1)

if len(sys.argv) > 1 and sys.argv[1] == '-d':
    print("Found no sagetest nodes to delete")
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
local-hostname: sagetest
"""

user_data = """#cloud-config
# runcmd only runs once, and will cause the node to shutdown so we can resize its disk
runcmd:
   - [ shutdown, -h, now ]

ssh_deletekeys: true
ssh_keys:
    rsa_private: |
        -----BEGIN OPENSSH PRIVATE KEY-----
        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
        -----END OPENSSH PRIVATE KEY-----
    rsa_public: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDA42U2T3CFYfSh4hlJg69bH37njgXT5RSrzWH1W93BhGvLhcsV5JSMF05krtWAnzinlcoEL7dQIQeaTS4Y9bmHlERR886RGnuIZWDcM+GZUCubCWyw2ogFolqQm55QaCjti9KMHv2CHkNFPE/nVqiY8iRvQ33zP+gDb76BV8+Mbs+TWJmj7kJXiwHcnePW0F0CYsJYaIDy4l+tmhYo2JrHWD5fIiGCgMCMPNehvbGTSJMBCJbehXdVZxew1HSvL6WbPezVD7Ffvs1Fq+fGtvLlLpABtuUH4CqF4b33fNp4lNZAo3g+WPy4rs5ylu49ItdcSc9hC2t3kJcXEvPIbwq9NTqbUCA3zO9mvrBzP/Io5ExZbhkC81FFilzh/v7vmt6NF0FnYuzRhfItc2pXfmcrEsiRLW9uAsojIV4UQlGf6bwHYHXvKV+DlRd4YAtNxzqcMY2V8ZsYyupBKrseD3l6OIhVCp2uKSiJKS+LYWvSheIUlykzJhaaNNMQevgVzh0= root@sagetest
    dsa_private: |
        -----BEGIN OPENSSH PRIVATE KEY-----
        b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABsgAAAAdzc2gtZH
        NzAAAAgQDjoigEAiHMvZ4YRfvy4a3yJLNtSa4wkJEUZi046hpbhouxhWyU9FYwKp0PjvPc
        MnNtOPeulhkxzGLucm43tfSTAKDJ2QpKr3kIqa6VkA2lBJtw8kXy+/CipXMyPCSe76nTTV
        VhPX5EBIEvv8FA9BIOg7BvTjuIrc8vHHUnXssW/QAAABUAyRtV+XTnjaKYBS6AUGLP+L8O
        ogkAAACBANhmnXcpCJr75wOAV7I7ru3ndSU+CrWWYncd/aqbbGeo8oSddSzmpPDbA//gg9
        SdPmSr7CZu2HQrnq25BLk9xn6s8hwMt9pJmNWBVoUki8LI3WMyxA0srP+ODf+wzhoGShuH
        QFW/e4savtbjJ9pneJZLTCg+sct1jshRBa0+zV5fAAAAgHdm68TQdPobVXidgY2TQNcAt+
        vL01u7bTBUtZSqZe9JW9DFLQSdBxTCuPNfsO6gXe9Ej9NP60dNwlLaE8KYFpw2Df9zaf0Q
        ntVOp0AC/dQbRblHTnZBwdKVJEAmmRfZj+dUV64DNrCWjEjb22cS26ALxamIZtNWltULhp
        dUhwMpAAAB6G0O9Y1tDvWNAAAAB3NzaC1kc3MAAACBAOOiKAQCIcy9nhhF+/LhrfIks21J
        rjCQkRRmLTjqGluGi7GFbJT0VjAqnQ+O89wyc204966WGTHMYu5ybje19JMAoMnZCkqveQ
        iprpWQDaUEm3DyRfL78KKlczI8JJ7vqdNNVWE9fkQEgS+/wUD0Eg6DsG9OO4itzy8cdSde
        yxb9AAAAFQDJG1X5dOeNopgFLoBQYs/4vw6iCQAAAIEA2GaddykImvvnA4BXsjuu7ed1JT
        4KtZZidx39qptsZ6jyhJ11LOak8NsD/+CD1J0+ZKvsJm7YdCuerbkEuT3GfqzyHAy32kmY
        1YFWhSSLwsjdYzLEDSys/44N/7DOGgZKG4dAVb97ixq+1uMn2md4lktMKD6xy3WOyFEFrT
        7NXl8AAACAd2brxNB0+htVeJ2BjZNA1wC368vTW7ttMFS1lKpl70lb0MUtBJ0HFMK481+w
        7qBd70SP00/rR03CUtoTwpgWnDYN/3Np/RCe1U6nQAL91BtFuUdOdkHB0pUkQCaZF9mP51
        RXrgM2sJaMSNvbZxLboAvFqYhm01aW1QuGl1SHAykAAAAUBgETgtOt3EM9TOubDQvflgPT
        uPcAAAANcm9vdEBzYWdldGVzdAECAwQF
        -----END OPENSSH PRIVATE KEY-----
    dsa_public: ssh-dss AAAAB3NzaC1kc3MAAACBAOOiKAQCIcy9nhhF+/LhrfIks21JrjCQkRRmLTjqGluGi7GFbJT0VjAqnQ+O89wyc204966WGTHMYu5ybje19JMAoMnZCkqveQiprpWQDaUEm3DyRfL78KKlczI8JJ7vqdNNVWE9fkQEgS+/wUD0Eg6DsG9OO4itzy8cdSdeyxb9AAAAFQDJG1X5dOeNopgFLoBQYs/4vw6iCQAAAIEA2GaddykImvvnA4BXsjuu7ed1JT4KtZZidx39qptsZ6jyhJ11LOak8NsD/+CD1J0+ZKvsJm7YdCuerbkEuT3GfqzyHAy32kmY1YFWhSSLwsjdYzLEDSys/44N/7DOGgZKG4dAVb97ixq+1uMn2md4lktMKD6xy3WOyFEFrT7NXl8AAACAd2brxNB0+htVeJ2BjZNA1wC368vTW7ttMFS1lKpl70lb0MUtBJ0HFMK481+w7qBd70SP00/rR03CUtoTwpgWnDYN/3Np/RCe1U6nQAL91BtFuUdOdkHB0pUkQCaZF9mP51RXrgM2sJaMSNvbZxLboAvFqYhm01aW1QuGl1SHAyk= root@sagetest

ssh_authorized_keys:
    - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCj6Vc0dUbmLEXByfgwtbG0teq+lhn1ZeCpBp/Ll+yapeTbdP0AuA9iZrcIi4O25ucy+VaZDutj2noNvkcq8dPrCmveX0Zxbylia7rNbd91DPU/94JRidElJPzB5eueObqiVWNWu1cGP0WdaHbecWy0Xu4fq+FqJn3z99Cg4XDYVsfP9avin6McHAaYItTmZHAuHgfL6hJCw4Ju0I7OMAlXgeb9S50nYpzN8ItbRmNQDZC3wdPs5iTd0LgGG/0P7ixhTWDSg5DeQc6JJ2rYezyzc1Lek3lQuBK6FiuvEyd99H2FrowN0b/n1pTQd//pq1G0AcGiwl0ttZ5i2HMe8sab baccala@max
"""

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

print("Waiting for node to start...")

node_url = "http://{}/v2/projects/{}/nodes/{}".format(gns3_server, project_id, ubuntu['node_id'])
result = requests.get(node_url, auth=auth)
result.raise_for_status()
while result.json()['status'] != 'started':
    time.sleep(1)
    result = requests.get(node_url, auth=auth)
    result.raise_for_status()

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

print("Extending disk by 16 GB...")

url = "http://{}/v2/compute/projects/{}/qemu/nodes/{}/resize_disk".format(gns3_server, project_id, ubuntu['node_id'])

resize_obj = {'drive_name' : 'hda', 'extend' : 16 * 1024}

result = requests.post(url, auth=auth, data=json.dumps(resize_obj))
result.raise_for_status()

print("Restarting the node...")
result = requests.post(project_start_url, auth=auth)
result.raise_for_status()
