#!/usr/bin/python3
#
# Script to start a Debian GNU/Hurd virtual machine in GNS3.
#
# Unlike the Ubuntu script, Hurd doesn't use cloud-init.  We just
# boot the pre-installed disk image directly.  The image comes with
# root login on the console (no password) and sshd running (with
# PermitRootLogin prohibit-password, so key-based only).
#
# After boot, the script uses the QEMU monitor's sendkey command to
# type commands on the console to install SSH authorized keys, then
# uses vncdo to take a screenshot showing the VM's IP address.
#
# The Hurd image must already be uploaded to the GNS3 server.
# Use upload-hurd-image.py to download and upload the latest image.
#
# RUNTIME DEPENDENCIES
#
# vncdotool must be installed for screenshots (pip3 install vncdotool)
#
# Usage:
#
#   ./hurd.py                               # boot with defaults
#   ./hurd.py -n myhurd                     # custom node name
#   ./hurd.py --smp 4                       # 4 CPUs
#   ./hurd.py --memory 8192                 # 8 GB RAM
#   ./hurd.py --image debian-hurd-amd64-20260314.img  # specific image
#   ./hurd.py --no-ssh-setup                # skip SSH key installation
#   ./hurd.py -d                            # delete existing node

import gns3

import sys
import os
import re
import time
import socket
import subprocess
import shutil

import argparse

SSH_AUTHORIZED_KEYS_FILES = ['~/.ssh/id_rsa.pub', '~/.ssh/id_ed25519.pub', '~/.ssh/authorized_keys']

parser = argparse.ArgumentParser(parents=[gns3.parser('hurd-test')],
                                 description='Start a Debian GNU/Hurd node in GNS3')

parser.add_argument('-n', '--name', default='hurd',
                    help='name of the Hurd node (default "hurd")')
parser.add_argument('--smp', type=int, default=1,
                    help='number of virtual CPUs (default 1)')
parser.add_argument('-m', '--memory', type=int, default=4096,
                    help='MBs of virtual RAM (default 4096)')
parser.add_argument('--image', default=None,
                    help='disk image filename (default: latest debian-hurd-amd64-*.img on server)')
parser.add_argument('--no-network', action='store_true',
                    help='do not create Internet cloud and switch')
parser.add_argument('--no-ssh-setup', action='store_true',
                    help='skip SSH key installation')
parser.add_argument('--reboot', action='store_true',
                    help='reboot an existing node (hard reset)')

args = parser.parse_args()

# Open the GNS3 server and project

gns3_server, gns3_project = gns3.open_project_with_standard_options(args)

# Handle --reboot: reboot an existing node and exit

if args.reboot:
    node = gns3_project.node(args.name)
    if not node:
        print(f"Node {args.name} not found")
        sys.exit(1)
    print(f"Rebooting {args.name} ...")
    gns3_project.reload_node(node)
    print(f"Node {args.name} rebooted")
    sys.exit(0)

# Find the Hurd image

if args.image:
    image = args.image
else:
    # Find the latest debian-hurd-amd64-*.img on the server
    images = gns3_server.images()
    hurd_images = sorted([img for img in images if re.match(r'debian-hurd-amd64-\d+\.img$', img)])
    if not hurd_images:
        print("No Hurd images found on GNS3 server. Run upload-hurd-image.py first.")
        sys.exit(1)
    image = hurd_images[-1]

print(f"Using image: {image}")

assert image in gns3_server.images(), f"Image {image} not found on GNS3 server"

# Check if node already exists

if gns3_project.node(args.name):
    print(f"Node {args.name} already exists")
    sys.exit(1)

# Collect SSH public keys

ssh_keys = []
for keyfile in SSH_AUTHORIZED_KEYS_FILES:
    keyfile = os.path.expanduser(keyfile)
    if os.path.exists(keyfile):
        with open(keyfile) as f:
            for line in f:
                line = line.strip()
                if line.startswith('ssh-'):
                    ssh_keys.append(line)

if not ssh_keys and not args.no_ssh_setup:
    print("Warning: no SSH public keys found; skipping SSH setup")
    args.no_ssh_setup = True

# Build extra QEMU options
#
# -M q35: Required for Hurd with larger RAM configurations (>4GB).
#         Safe to use unconditionally.
# -no-reboot: Halt instead of rebooting on crash, so we can inspect state.

qemu_options = "-M q35 -no-reboot"

if args.smp > 1:
    qemu_options += f" -smp {args.smp}"

# Create the node
#
# Hurd uses e1000 for networking (netdde or in-kernel drivers),
# not virtio-net-pci.
#
# Hurd uses IDE for disk access via rumpdisk, not scsi.
#
# We use VNC console so we can take screenshots and so QEMU provides
# a VGA display for gnumach (which doesn't use serial console by
# default).  Commands are typed via the QEMU monitor's sendkey
# command, which works reliably for all characters.

properties = {
    "adapter_type": "e1000",
    "hda_disk_interface": "ide",
    "ram": args.memory,
    "cpus": args.smp,
    "options": qemu_options,
}

config = {"console_type": "vnc"}

hurd_node = gns3_project.create_raw_qemu_node(args.name, image,
                                                properties=properties,
                                                config=config)

# Set up Internet access (unless --no-network)

if not args.no_network:
    cloud = gns3_project.cloud('Internet', args.interface, x=-300, y=0)
    switch = gns3_project.switch('InternetSwitch', x=0, y=0)
    gns3_project.link(cloud, 0, switch)
    gns3_project.link(hurd_node, 0, switch)

# Start the node

print(f"Starting {args.name} ...")
gns3_project.start_node(hurd_node, quiet=True)

# Get console info

gns3_project.nodes()  # refresh cache
node_info = gns3_project.node(args.name)
vnc_port = node_info['console']

print(f"Node {args.name} is running (VNC port {vnc_port})")

if args.no_ssh_setup or args.no_network:
    print(f"Connect via VNC to localhost::{vnc_port}")
    print(f"Login: root (no password)")
    sys.exit(0)

# ---------------------------------------------------------------------------
# SSH key setup via QEMU monitor sendkey
#
# vncdo's 'type' command mangles underscores, angle brackets, and other
# special characters.  Instead, we use the QEMU monitor's 'sendkey'
# command which sends raw keycodes and works reliably for all characters.
#
# The QEMU monitor port is found from the running QEMU process.
# ---------------------------------------------------------------------------

# Find the QEMU monitor port from the process command line

def find_monitor_port(node_id):
    """Find the QEMU monitor TCP port for a given GNS3 node."""
    import subprocess
    result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for line in result.stdout.split('\n'):
        if node_id in line and '-monitor' in line:
            match = re.search(r'-monitor tcp:127\.0\.0\.1:(\d+)', line)
            if match:
                return int(match.group(1))
    return None

monitor_port = find_monitor_port(hurd_node['node_id'])
if monitor_port is None:
    print("Could not find QEMU monitor port")
    print(f"Connect via VNC to localhost::{vnc_port}")
    print(f"Login: root (no password)")
    sys.exit(1)

print(f"QEMU monitor on port {monitor_port}")

# QEMU monitor sendkey character mapping

SENDKEY_MAP = {
    ' ': 'spc', '\n': 'ret', '\t': 'tab',
    '-': 'minus', '=': 'equal', '[': 'bracket_left', ']': 'bracket_right',
    '\\': 'backslash', ';': 'semicolon', "'": 'apostrophe',
    ',': 'comma', '.': 'dot', '/': 'slash', '`': 'grave_accent',
    '~': 'shift-grave_accent', '!': 'shift-1', '@': 'shift-2',
    '#': 'shift-3', '$': 'shift-4', '%': 'shift-5', '^': 'shift-6',
    '&': 'shift-7', '*': 'shift-8', '(': 'shift-9', ')': 'shift-0',
    '_': 'shift-minus', '+': 'shift-equal', '{': 'shift-bracket_left',
    '}': 'shift-bracket_right', '|': 'shift-backslash', ':': 'shift-semicolon',
    '"': 'shift-apostrophe', '<': 'shift-comma', '>': 'shift-dot',
    '?': 'shift-slash',
}

def monitor_sendkeys(sock, text):
    """Type text on the QEMU console via monitor sendkey commands."""
    for ch in text:
        if ch in SENDKEY_MAP:
            key = SENDKEY_MAP[ch]
        elif ch.isupper():
            key = f'shift-{ch.lower()}'
        elif ch.isalnum():
            key = ch
        else:
            print(f"Warning: unmapped character {ch!r}, skipping")
            continue
        sock.send(f'sendkey {key}\n'.encode())
        time.sleep(0.02)

def monitor_cmd(sock, text):
    """Type a shell command and press enter."""
    monitor_sendkeys(sock, text)
    time.sleep(0.1)
    sock.send(b'sendkey ret\n')

# Connect to the QEMU monitor

monitor = socket.socket()
monitor.connect(('127.0.0.1', monitor_port))
monitor.settimeout(2)
try:
    monitor.recv(4096)  # drain prompt
except socket.timeout:
    pass

# Wait for boot

print("Waiting for boot (90 seconds) ...")
time.sleep(90)

# Log in as root

print("Logging in as root ...")
monitor.send(b'sendkey ret\n')
time.sleep(1)
monitor_cmd(monitor, 'root')
time.sleep(3)

# Install SSH keys

print("Installing SSH authorized keys ...")
monitor_cmd(monitor, 'mkdir -p /root/.ssh')
time.sleep(1)
monitor_cmd(monitor, 'chmod 700 /root/.ssh')
time.sleep(1)

for key in ssh_keys:
    monitor_cmd(monitor, f"echo '{key}' >> /root/.ssh/authorized_keys")
    time.sleep(1)

monitor_cmd(monitor, 'chmod 600 /root/.ssh/authorized_keys')
time.sleep(1)

# Verify
monitor_cmd(monitor, 'wc -l /root/.ssh/authorized_keys')
time.sleep(2)

# Install locale to suppress SSH locale warnings.
# We detect the user's LANG and install that locale on the Hurd VM.
# This requires network access (apt-get install locales), so we do it
# best-effort and warn if it fails.

lang = os.environ.get('LANG', '')
if lang and not args.no_network:
    # Parse LANG (e.g. "en_US.UTF-8") into localedef arguments
    # localedef -i en_US -c -f UTF-8 en_US.UTF-8
    if '.' in lang:
        lang_base, lang_encoding = lang.split('.', 1)
    else:
        lang_base = lang
        lang_encoding = 'UTF-8'

    print(f"Installing locale {lang} (this takes a couple minutes) ...")
    # Chain all commands with && so the shell sequences them.
    # We wait long enough for apt-get update + install + localedef to finish.
    monitor_cmd(monitor, f'apt-get update -qq 2>/dev/null && apt-get install -y -qq locales 2>/dev/null && localedef -i {lang_base} -c -f {lang_encoding} {lang} && echo LOCALE-DONE')
    time.sleep(120)

# Get IP address
print("Getting IP address ...")
monitor_cmd(monitor, 'ifconfig /dev/eth0')
time.sleep(3)

monitor.close()

# Take a screenshot showing the result

screenshot = f'/tmp/hurd-{args.name}-ip.png'
if shutil.which('vncdo'):
    subprocess.run(['vncdo', '-s', f'localhost::{vnc_port}', 'capture', screenshot],
                   capture_output=True)
    print(f"\nVNC screenshot saved to {screenshot}")

print()
print(f"SSH keys installed for root.")
print(f"Check the screenshot for the IP address, then:")
print(f"  ssh root@<ip-address>")
print()
print(f"Or connect via VNC at localhost::{vnc_port}")
