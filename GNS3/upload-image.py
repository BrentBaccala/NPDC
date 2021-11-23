#!/usr/bin/python3
#
# Script to upload an image to GNS3.
#
# USAGE
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

import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth
from requests_toolbelt.streaming_iterator import StreamingIterator

import argparse

import configparser

GNS3_CREDENTIAL_FILES = ["~/gns3_server.conf", "~/.config/GNS3/2.2/gns3_server.conf"]
SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', "~/.ssh/authorized_keys"]

# Location of the GNS3 server.  Needed to copy disk files if building a GNS3 appliance.
GNS3_HOME = '/home/gns3'

GNS3_APPLIANCE_FILE = 'opendesktop.gns3a'

# Parse the command line options

parser = argparse.ArgumentParser(description='Upload a qemu image file to GNS3')
group = parser.add_mutually_exclusive_group()
group.add_argument('--ls', action="store_true",
                    help='list existing images')
group.add_argument('filename', type=str, nargs='?',
                    help='filename to upload')
parser.add_argument('--overwrite', action="store_true",
                    help='overwrite existing image')
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

url = "http://{}/v2/compute/qemu/images".format(gns3_server)
result = requests.get(url, auth=auth)
result.raise_for_status()
images = result.json()
image_filenames = [j['filename'] for j in images]

if args.ls:
    for image in images:
        print(image['filename'])
    exit(0)

if args.filename in image_filenames and not args.overwrite:
    print("Won't overwrite existing image")
    exit(1)

if args.filename:
    with open(args.filename, 'rb') as f:
        url = "http://{}/v2/compute/qemu/images/{}".format(gns3_server, args.filename)
        size = os.stat(args.filename).st_size
        streamer = StreamingIterator(size, f)
        result = requests.post(url, auth=auth, data=streamer, headers={'Content-Type': 'application/octet-stream'})
        result.raise_for_status()
