#!/usr/bin/python3
#
# Download a Debian GNU/Hurd disk image (.tar.xz) and upload the
# extracted raw .img to the GNS3 server in a single streaming pass.
#
# The tar.xz is ~355MB; the raw image inside is ~3.9GB.  We decompress
# and extract on the fly so neither the compressed nor the full image
# needs to be staged on disk.
#
# Usage:
#
#   ./upload-hurd-image.py                  # auto-detect and upload latest image
#   ./upload-hurd-image.py --overwrite      # replace existing image on server
#   ./upload-hurd-image.py --ls             # list images on server
#   ./upload-hurd-image.py --url URL        # use a specific .tar.xz URL

import os
import re
import sys
import gns3
import lzma
import tarfile
import requests

try:
    from requests_toolbelt.streaming_iterator import StreamingIterator
except ModuleNotFoundError as e:
    raise ModuleNotFoundError('On Ubuntu (and maybe other Debian-based systems), you should run "apt install python3-requests-toolbelt"') from e

import argparse

try:
    from clint.textui.progress import Bar as ProgressBar
except ModuleNotFoundError:
    ProgressBar = None

HURD_IMAGE_DIR = 'https://cdimage.debian.org/cdimage/ports/latest/hurd-amd64/'

def find_latest_image_url():
    """Fetch the directory listing and find the latest .img.tar.xz file."""
    response = requests.get(HURD_IMAGE_DIR)
    response.raise_for_status()
    # Look for .img.tar.xz files with a date stamp in the name
    matches = re.findall(r'href="(debian-hurd-amd64-\d+\.img\.tar\.xz)"', response.text)
    if not matches:
        print("Could not find any .img.tar.xz files at", HURD_IMAGE_DIR)
        sys.exit(1)
    latest = sorted(matches)[-1]
    return HURD_IMAGE_DIR + latest

parser = argparse.ArgumentParser(description='Download and upload a Debian Hurd image to GNS3')
parser.add_argument('-H', '--host', help='name of the GNS3 host')
parser.add_argument('--url', default=None, help='URL of a specific .tar.xz image (default: auto-detect latest)')
parser.add_argument('--overwrite', action='store_true', help='overwrite existing image on GNS3 server')
parser.add_argument('--ls', action='store_true', help='list available images on GNS3 server')
args = parser.parse_args()

gns3_server = gns3.Server(host=args.host)

if args.ls:
    for img in sorted(gns3_server.images()):
        print(img)
    sys.exit(0)

# Find the latest image if no URL was specified

if args.url is None:
    print(f"Checking {HURD_IMAGE_DIR} for latest image ...")
    args.url = find_latest_image_url()

# Download the tar.xz, decompress, extract the .img, and stream it to GNS3

print(f"Downloading {args.url} ...")
response = requests.get(args.url, stream=True)
response.raise_for_status()

# Decompress xz and open as a streaming tar archive
xz_stream = lzma.open(response.raw)
tar = tarfile.open(fileobj=xz_stream, mode='r|')

img_member = None
for member in tar:
    if member.name.endswith('.img'):
        img_member = member
        break

if img_member is None:
    print("No .img file found in the archive")
    sys.exit(1)

img_name = os.path.basename(img_member.name)
img_size = img_member.size

print(f"Found {img_name} ({img_size / (1024**3):.2f} GB)")

if img_name in gns3_server.images() and not args.overwrite:
    print(f"Image {img_name} already exists on GNS3 server (use --overwrite to replace)")
    sys.exit(1)

img_stream = tar.extractfile(img_member)

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

print(f"Uploading {img_name} to GNS3 server ...")
if not ProgressBar:
    print("(install python3-clint for a progress bar)")

url = "{}/compute/qemu/images/{}".format(gns3_server.url, img_name)

with StreamingIteratorWithProgressBar(img_size, img_stream) as streamer:
    result = requests.post(url, auth=gns3_server.auth, data=streamer,
                           headers={'Content-Type': 'application/octet-stream'})
    result.raise_for_status()

print(f"Uploaded {img_name} successfully")
