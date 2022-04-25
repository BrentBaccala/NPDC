#!/usr/bin/python3
#
# Script to add an image to a GNS3 appliance file.

import json
import os
import hashlib
import argparse

GNS3_APPLIANCE_FILE = 'opendesktop.gns3a'

# Utility function used when generating a GNS3 appliance

def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

# Parse the command line options

parser = argparse.ArgumentParser(description='Add an image to a GNS3 appliance file')
parser.add_argument('-n', '--name', type=str, required=True,
                    help='name of this version')
parser.add_argument('filename', type=str, nargs=1,
                    help='filename to add')
args = parser.parse_args()

appliance_image_filename = args.filename[0]

if os.path.exists(GNS3_APPLIANCE_FILE):
    print("Appending to GNS3 appliance file...")
    with open(GNS3_APPLIANCE_FILE) as f:
        gns3_appliance_json = json.load(f)

gns3_appliance_json['images'].append({'filename': appliance_image_filename,
                                      'version': 1,
                                      'md5sum': md5(appliance_image_filename),
                                      'filesize': os.stat(appliance_image_filename).st_size
})
gns3_appliance_json['versions'].append({'name': args.name,
                                        'images': {'hda_disk_image': appliance_image_filename}
})
with open(GNS3_APPLIANCE_FILE, 'w') as f:
    json.dump(gns3_appliance_json, f, indent=4)
    # put a newline at the end of file, which json.dump doesn't do
    print(file=f)
