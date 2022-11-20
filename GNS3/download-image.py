#!/usr/bin/python3

import os
import gns3
import json
import shutil
import tempfile
import requests
import argparse
import datetime
import subprocess

# Location of the GNS3 server.  Needed to copy disk files if building a GNS3 appliance.
GNS3_HOME = '/home/gns3'

# use 'ubuntu-test' as project name, because that's the most common choice to download images from
parser = argparse.ArgumentParser(parents=[gns3.parser('ubuntu-test')], description='Download and rebase GNS3 image')
args = parser.parse_args()

# Open the GNS3 server

gns3_server, gns3_project = gns3.open_project_with_standard_options(args)

# fetch node id

node_id = next(node for node in gns3_project.nodes() if node['name'] == 'ubuntu')['node_id']
project_id = gns3_project.project_id

disk_UUID_filename = os.path.join(GNS3_HOME, 'GNS3/projects/{}/project-files/qemu/{}/hda_disk.qcow2'.format(project_id, node_id))
now = datetime.datetime.now()
appliance_image_filename = now.strftime('ubuntu-open-desktop-%Y-%h-%d-%H%M.qcow2')
subprocess.run(['cp', disk_UUID_filename, appliance_image_filename]).check_returncode()

disk_info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', disk_UUID_filename]))

# 6a. get a copy of the backing image and rebase it
#     from https://stackoverflow.com/a/39217788/1493790
url = "{}/compute/qemu/images/{}".format(gns3_server.url, os.path.basename(disk_info['backing-filename']))
with tempfile.NamedTemporaryFile() as tmp:
    with requests.get(url, auth=gns3_server.auth, stream=True) as r:
        shutil.copyfileobj(r.raw, tmp)
    subprocess.run(['qemu-img', 'rebase', '-u', '-b', tmp.name, appliance_image_filename]).check_returncode()
    subprocess.run(['qemu-img', 'rebase', '-b', "", appliance_image_filename]).check_returncode()

# 6c. would work if we have permission to read the backing file, which we typically do not (current GNS3 permissions)
# subprocess.run(['qemu-img', 'rebase', '-b', "", appliance_image_filename]).check_returncode()

# 6b. rebase the image (need read permission on backing file)
#    Can you skip this step?  Yes, but rebased file is just less than 1 GB bigger than the original, so that's all you save.
#    Plus, if you skip this, you have a file that can only be used on the same system, or one with an idential backing file.
#    Remember that those cloudimg files (the backing file) are updated by Canoncial every few days.
# subprocess.run(['qemu-img', 'rebase', '-b', "", appliance_image_filename]).check_returncode()
print("New appliance image created:", appliance_image_filename)
