#!/usr/bin/python3
#
# Script to upload an image to GNS3.

import os
import sys
import gns3
import requests
from requests_toolbelt.streaming_iterator import StreamingIterator

import argparse

# Parse the command line options

parser = argparse.ArgumentParser(description='Upload a file to GNS3')
parser.add_argument('-H', '--host',
                    help='name of the GNS3 host')
parser.add_argument('--overwrite', action="store_true",
                    help='overwrite existing image')
# Doesn't work because gns3 doesn't (yet) implement DELETE method for images
#parser.add_argument('--delete', action="store_true",
#                    help='delete existing image')
group = parser.add_mutually_exclusive_group()
group.add_argument('--ls', action="store_true",
                    help='list availabel images')
group.add_argument('filename', type=str, nargs='?',
                    help='filename to upload')
args = parser.parse_args()

gns3_server = gns3.Server(host=args.host)

if args.ls:
    print(gns3_server.images())
    exit(0)

if args.filename in gns3_server.images() and not args.overwrite:
    print("Won't overwrite existing image")
    exit(1)

if args.filename:
    with open(args.filename, 'rb') as f:
        url = "{}/compute/qemu/images/{}".format(gns3_server.url, os.path.basename(args.filename))
        size = os.stat(args.filename).st_size
        streamer = StreamingIterator(size, f)
        result = requests.post(url, auth=gns3_server.auth, data=streamer, headers={'Content-Type': 'application/octet-stream'})
        result.raise_for_status()
