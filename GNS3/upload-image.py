#!/usr/bin/python3
#
# Script to upload an image to GNS3.

import os
import sys
import gns3
import requests
from requests_toolbelt.streaming_iterator import StreamingIterator

import argparse

# "apt install python3-clint" for a progress bar, but don't require it

try:
    from clint.textui.progress import Bar as ProgressBar
except ModuleNotFoundError:
    ProgressBar = None

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

if os.path.basename(args.filename) in gns3_server.images() and not args.overwrite:
    print("Won't overwrite existing image")
    exit(1)

class StreamingIteratorWithProgressBar(StreamingIterator):
    def __init__(self, size, iterator, **kwargs):
        StreamingIterator.__init__(self, size, iterator, **kwargs)
        if ProgressBar:
            self.bar = ProgressBar(expected_size=size)
            self.bytes_read = 0
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if ProgressBar:
            self.bar.done()
        return False
    def read(self, size=-1):
        if ProgressBar:
            if size >= 0:
                self.bytes_read += size
            else:
                self.bytes_read = self.size
            self.bar.show(self.bytes_read)
        return StreamingIterator.read(self, size)

if args.filename:
    with open(args.filename, 'rb') as f:
        print("uploading", args.filename)
        if not ProgressBar:
            print("clint package not available; no progress bar will be displayed")
        url = "{}/compute/qemu/images/{}".format(gns3_server.url, os.path.basename(args.filename))
        size = os.stat(args.filename).st_size
        with StreamingIteratorWithProgressBar(size, f) as streamer:
            result = requests.post(url, auth=gns3_server.auth, data=streamer,
                                   headers={'Content-Type': 'application/octet-stream'})
            result.raise_for_status()
