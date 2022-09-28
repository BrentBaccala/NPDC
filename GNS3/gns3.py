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
# We use an Ubuntu cloud image and a client image created using the
# GNS3/ubuntu.py script in BrentBaccala's NPDC github repository.

import sys
import requests
from requests.auth import HTTPBasicAuth
import json
import yaml
import os
import tempfile
import urllib.parse
import ipaddress

import socket
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer

import subprocess

import configparser

# The files we search for GNS3 credentials.
#
# The first file whose host and port match the supplied parameters is used.

GNS3_CREDENTIAL_FILES = ["~/gns3_server.conf",
                         "~/.config/GNS3/2.2/gns3_server.conf",
                         "~/.config/GNS3/2.2/profiles/*/gns3_server.conf"]

# Find out if the system we're running on is configured to use an apt proxy.

apt_proxy = None
apt_config_command = ['apt-config', '--format', '%f %v%n', 'dump']
apt_config_proc = subprocess.Popen(apt_config_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
for config_line in apt_config_proc.stdout.read().decode().split('\n'):
    if ' ' in config_line:
        key,value = config_line.split(' ', 1)
        if key == 'Acquire::http::Proxy':
            apt_proxy = value

class Server:

    def __init__(self, host=None, port=None, user=None, password=None, verbose=True):
        self.url = None
        self.auth = None
        self.verbose = verbose

        # Obtain the credentials needed to authenticate ourself to the GNS3 server
        #
        # Look through the credential files for the first Server entry that matches
        # 'host' and 'port', or just the first entry if those two are None.

        for propfilename in GNS3_CREDENTIAL_FILES:
            propfilename = os.path.expanduser(propfilename)
            if os.path.exists(propfilename):
                config = configparser.ConfigParser()
                try:
                    config.read(propfilename)
                    if not host or host == config['Server']['host']:
                        if not port or port == config['Server']['port']:
                            self.url = "http://{}:{}/v2".format(config['Server']['host'], config['Server']['port'])
                            self.auth = HTTPBasicAuth(config['Server']['user'], config['Server']['password'])
                except:
                    pass

        if not self.url or not self.auth:
            raise Exception("No matching GNS3 server configuration found")

    def images(self):
        # GNS3 doesn't seem to support a HEAD method on its images, so we get
        # a directory of all of them and search for the ones we want

        url = "{}/compute/qemu/images".format(self.url)
        return [f['filename'] for f in requests.get(url, auth=self.auth).json()]


    def projects(self):

        url = "{}/projects".format(self.url)

        result = requests.get(url, auth=self.auth)
        result.raise_for_status()
        return result.json()

    def project_names(self):

        return [p['name'] for p in self.projects()]

    def project(self, project_name, create=False):
        for project in self.projects():
            if project['name'] == project_name:
                return Project(self, project['project_id'])
        if create:
            new_project = {'name': project_name, 'auto_close' : False}
            url = "{}/projects".format(self.url)
            result = requests.post(url, auth=self.auth, data=json.dumps(new_project))
            result.raise_for_status()
            return Project(self, result.json()['project_id'])
        else:
            raise Exception("GNS3 project does not exist")

    # This will return the local IP address that the script uses to
    # connect to the GNS3 server.  We need this to tell the instance
    # how to connect back to the script, and if we've got multiple
    # interfaces, multiple DNS names, and multiple IP addresses, it's a
    # bit unclear which one to use.
    #
    # from https://stackoverflow.com/a/28950776/1493790

    def get_local_ip(self):
        "Return the local IP address used to connect to the server"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # The address doesn't even have to be reachable, since a UDP connect
        # doesn't send any packets.
        s.connect((urllib.parse.urlparse(self.url).hostname, 1))
        IP = s.getsockname()[0]
        s.close()
        return IP

# RequestHandler for an HTTP server running that will receive
# notifications from the instances after they complete cloud-init.
#
# This assumes that the virtual topology will have connectivity with
# the host running this script (this is what the -I interface is for).
#
# We keep a set of which instances have reported in, and a condition
# variable is used to signal our main thread when they report.
# These are extra instance variables on the server object:
#     self.server.instances_reported
#     self.server.instance_report_cv

class RequestHandler(BaseHTTPRequestHandler):
    # cloud-init does a POST; expect a URL query string with a 'hostname'
    def do_POST(self):
        length = self.headers['Content-Length']
        self.send_response_only(100)
        self.end_headers()

        content = urllib.parse.parse_qs(self.rfile.read(int(length)))
        hostname = content[b'hostname'][0].decode()
        print(hostname, 'running')

        with self.server.instance_report_cv:
            if not hostname in self.server.instances_reported:
                self.server.instances_reported[hostname] = self.client_address[0]
                self.server.instance_content[hostname] = content
                self.server.instance_report_cv.notify()

        self.send_response(200)
        self.end_headers()

    # Cisco CSR100V does a PUT; expect hostname in the URL
    def do_PUT(self):
        length = self.headers['Content-Length']
        self.send_response_only(100)
        self.end_headers()

        content = urllib.parse.parse_qs(self.rfile.read(int(length)))
        hostname = self.path.split('/')[-1]
        print(hostname, 'running')

        with self.server.instance_report_cv:
            if not hostname in self.server.instances_reported:
                self.server.instances_reported[hostname] = self.client_address[0]
                self.server.instance_content[hostname] = content
                self.server.instance_report_cv.notify()

        self.send_response(200)
        self.end_headers()

class Project:

    def __init__(self, server, project_id):
        self.server = server
        self.project_id = project_id
        self.url = "{}/projects/{}".format(server.url, project_id)
        self.auth = server.auth
        self.verbose = server.verbose
        self.cached_nodes = None

        self.local_ip = server.get_local_ip()

        if self.local_ip != '127.0.0.1':
            server_address = ('', 0)
            self.httpd = HTTPServer(server_address, RequestHandler)
            self.httpd.instances_reported = {}
            self.httpd.instance_content = {}
            self.httpd.instance_report_cv = threading.Condition()
            self.notification_url = "http://{}:{}/".format(self.local_ip, self.httpd.server_port)
        else:
            # No point in trying notification callbacks to localhost; it won't work
            self.httpd = None
            self.notification_url = None

    def open(self):
        if self.verbose: print("Opening project", self.project_id)
        url = "{}/open".format(self.url)
        result = requests.post(url, auth=self.auth, data=json.dumps({}))
        result.raise_for_status()

    def close(self):
        if self.verbose: print("Closing project", self.project_id)
        url = "{}/close".format(self.url)
        result = requests.post(url, auth=self.auth, data=json.dumps({}))
        result.raise_for_status()

    def remove(self):
        result = requests.delete(self.url)
        result.raise_for_status()

    def nodes(self):
        "Returns a list of dictionaries, each corresponding to a single gns3 node"

        url = "{}/nodes".format(self.url)
        result = requests.get(url, auth=self.auth)
        result.raise_for_status()
        self.cached_nodes = result.json()
        return self.cached_nodes

    def links(self):
        "Returns a list of dictionaries, each corresponding to a single gns3 link"

        links_url = "{}/links".format(self.url)
        result = requests.get(links_url, auth=self.auth)
        result.raise_for_status()
        return result.json()

    def delete_everything(self):
        "Delete all nodes in a project"
        for node in self.nodes():
            if self.verbose: print("Deleting node", node['name'])
            node_url = "{}/nodes/{}".format(self.url, node['node_id'])
            result = requests.delete(node_url, auth=self.auth)
            result.raise_for_status()

    def delete_substring(self, substring):
        "Delete all nodes in a project whose name contain a given substring"
        for node in self.nodes():
            if substring in node['name']:
                if self.verbose: print("Deleting node", node['name'])
                node_url = "{}/nodes/{}".format(self.url, node['node_id'])
                result = requests.delete(node_url, auth=self.auth)
                result.raise_for_status()

    ### TRACK WHICH OBJECTS DEPEND ON WHICH OTHERS FOR START ORDER

    # a map from nodeID to a list of node dictionaries
    node_dependencies = dict()

    def depends_on(self, node1, node2):
        print('depending', node1['name'], 'on', node2['name'])
        self.node_dependencies[node1['node_id']] = (node2,)

    ### Start nodes running
    ###
    ### We can't start everything at once, because not having network connectivity during boot is a problem
    ### for things like package installs and upgrades, so we need to make sure the gateways come up first
    ### before we try to boot nodes deeper in the topology.

    def start_all_nodes(self):
        project_start_url = "{}/nodes/start".format(self.url)
        result = requests.post(project_start_url, auth=self.auth)
        result.raise_for_status()

    def start_nodeid(self, nodeid):
        existing_nodes = self.nodes()
        names_by_node_id = {node['node_id']:node['name'] for node in existing_nodes}
        print(f"Starting {names_by_node_id[nodeid]}...")

        project_start_url = "{}/nodes/{}/start".format(self.url, nodeid)
        result = requests.post(project_start_url, auth=self.auth)
        result.raise_for_status()

    def start_node(self, node):
        self.start_nodeid(node['node_id'])

    def start_nodes(self, *node_list):

        # node_list can be either names or node dictionaries
        node_names_to_start = [node['name'] if type(node) == dict else node for node in node_list]

        threading.Thread(target=self.httpd.serve_forever).start()

        existing_nodes = self.nodes()

        names_by_node_id = {node['node_id']:node['name'] for node in existing_nodes}
        node_ids_by_name = {node['name']:node['node_id'] for node in existing_nodes}

        all_dependent_nodes = set()
        for value in self.node_dependencies.values():
            for node in value:
                all_dependent_nodes.update((node['node_id'], ))

        # We assume that if GNS3 reported the node as 'started', that it's ready for service.
        # This isn't entirely valid, as it might still be booting, but it's OK for now (I hope).

        running_nodeids = set(node['node_id'] for node in existing_nodes if node['status'] == 'started')

        waiting_for_nodeids_to_start = set()

        for node_name in node_names_to_start:
            node_id = node_ids_by_name[node_name]
            dependencies = self.node_dependencies.get(node_id, [])
            # we'll need to start all nodes dependent on the nodes to start
            for v in dependencies:
                if v['name'] not in node_names_to_start:
                    node_names_to_start.append(v['name'])
            # if the node isn't running but all of its dependencies are, start it
            if node_id not in running_nodeids and node_id not in waiting_for_nodeids_to_start:
                if running_nodeids.issuperset([v['node_id'] for v in dependencies]):
                    self.start_nodeid(node_id)
                    waiting_for_nodeids_to_start.add(node_id)

        with self.httpd.instance_report_cv:
            # maybe this should be just "while waiting_for_nodeids_to_start:"
            # the way it's done now, we only keep waiting until all dependent nodes have started
            # so, for example, if a server depends on a gateway, we wait for the gateway
            #    to boot, then start the server, but return without waiting for the server
            #    to finish booting
            while waiting_for_nodeids_to_start:
            #while waiting_for_nodeids_to_start.intersection(all_dependent_nodes):

                #print('Waiting for', [names_by_node_id[nodeid] for nodeid in waiting_for_nodeids_to_start.intersection(all_dependent_nodes)])
                print('Waiting for', [names_by_node_id[nodeid] for nodeid in waiting_for_nodeids_to_start])
                self.httpd.instance_report_cv.wait()

                running_nodeids.update(node_ids_by_name[inst] for inst in self.httpd.instances_reported)

                waiting_for_nodeids_to_start.difference_update(running_nodeids)

                candidate_nodes = set()

                for key, value in self.node_dependencies.items():
                    if key not in waiting_for_nodeids_to_start and key not in running_nodeids:
                        if running_nodeids.issuperset([v['node_id'] for v in value]):
                            candidate_nodes.add(key)

                for start_node in candidate_nodes:
                    self.start_nodeid(start_node)
                    waiting_for_nodeids_to_start.add(start_node)

        self.httpd.shutdown()

    ### FUNCTIONS TO CREATE VARIOUS KINDS OF GNS3 OBJECTS

    def create_qemu_node(self, name, image, images=[], properties={}, config={}, disk=None):
        r"""create_qemu_node(name, image, images, properties, config, disk)
        images are files to place in the ISO image (a dictionary mapping file names to data)
        properties are additional items to add to the properties structure
        config are additional items to add to the qemnu node structure
        disk is a disk size is MB (default is to not resize the default image)
        """
        # Create an ISO image containing the boot configuration and upload it
        # to the GNS3 project.  We write the config to a temporary file,
        # convert it to ISO image, then post the ISO image to GNS3.

        assert image

        print(f"Building ISO configuration for {name}...")

        # Generate the ISO image that will be used as a virtual CD-ROM to pass all this initialization data to cloud-init.

        genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                               "-relaxed-filenames", "-V", "cidata", "-graft-points"]

        temporary_files = []

        for fn,data in images.items():

            data_file = tempfile.NamedTemporaryFile(delete = False)
            data_file.write(data)
            data_file.close()
            genisoimage_command.append(f"{fn}={data_file.name}")
            temporary_files.append(data_file)

        genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        isoimage = genisoimage_proc.stdout.read()

        debug_isoimage = False
        if debug_isoimage:
            with open('isoimage-debug.iso', 'wb') as f:
                f.write(isoimage)

        for tmpfile in temporary_files:
            os.remove(tmpfile.name)

        print(f"Uploading ISO configuration for {name}...")

        # files in the GNS3 directory take precedence over these project files,
        # so we need to make these file names unique
        cdrom_image = self.project_id + '_' + name + '.iso'
        file_url = "{}/files/{}".format(self.url, cdrom_image)
        result = requests.post(file_url, auth=self.auth, data=isoimage)
        result.raise_for_status()

        # Configure a QEMU cloud node

        print(f"Configuring {name} node...")

        url = "{}/nodes".format(self.url)

        # It's important to use the scsi disk interface, because the IDE interface in qemu
        # has some kind of bug, probably in its handling of DISCARD operations, that
        # causes a thin provisioned disk to balloon up with garbage.
        #
        # See https://unix.stackexchange.com/questions/700050
        # and https://bugs.launchpad.net/ubuntu/+source/qemu/+bug/1974100

        qemu_node = {
            "compute_id": "local",
            "name": name,
            "node_type": "qemu",
            "properties": {
                "adapter_type" : "virtio-net-pci",
                "hda_disk_image": image,
                "hda_disk_interface": "scsi",
                "cdrom_image" : cdrom_image,
                "qemu_path": "/usr/bin/qemu-system-x86_64",
#                "process_priority": "very high",
            },

            # ens4, ens5, ens6 seems to be the numbering scheme on Ubuntu 20,
            # but we can't replicate that with a Python format string
            "port_name_format": "eth{}",

            "symbol": ":/symbols/qemu_guest.svg",
        }

        qemu_node['properties'].update(properties)
        qemu_node.update(config)

        result = requests.post(url, auth=self.auth, data=json.dumps(qemu_node))
        result.raise_for_status()
        qemu = result.json()

        if disk and disk > 2048:
            url = "{}/compute/projects/{}/qemu/nodes/{}/resize_disk".format(self.url, qemu['node_id'])
            resize_obj = {'drive_name' : 'hda', 'extend' : disk - 2048}
            result = requests.post(url, auth=self.auth, data=json.dumps(resize_obj))
            result.raise_for_status()

        self.nodes()  # update self.cached_nodes
        return qemu

    def create_ubuntu_node(self, user_data, network_config=None, x=0, y=0, image=None, cpus=None, ram=None, disk=None, ethernets=None, vnc=None):
        r"""create_ubuntu_node(user_data, x=0, y=0, cpus=None, ram=None, disk=None)
        ram and disk are both in MB; ram defaults to 256 MB; disk defaults to 2 GB
        """
        # Create an ISO image containing the boot configuration and upload it
        # to the GNS3 project.  We write the config to a temporary file,
        # convert it to ISO image, then post the ISO image to GNS3.

        assert image

        print(f"Building cloud-init configuration for {user_data['hostname']}...")

        # Putting local-hostname in meta-data ensures that any initial DHCP will be done with hostname, not 'ubuntu'
        meta_data = {'local-hostname': user_data['hostname']}

        # Generate the ISO image that will be used as a virtual CD-ROM to pass all this initialization data to cloud-init.

        meta_data_file = tempfile.NamedTemporaryFile(delete = False)
        meta_data_file.write(yaml.dump(meta_data).encode('utf-8'))
        meta_data_file.close()

        user_data_file = tempfile.NamedTemporaryFile(delete = False)
        user_data_file.write(("#cloud-config\n" + yaml.dump(user_data)).encode('utf-8'))
        user_data_file.close()

        genisoimage_command = ["genisoimage", "-input-charset", "utf-8", "-o", "-", "-l",
                               "-relaxed-filenames", "-V", "cidata", "-graft-points",
                               "meta-data={}".format(meta_data_file.name),
                               "user-data={}".format(user_data_file.name)]

        if network_config:
            network_config_file = tempfile.NamedTemporaryFile(delete = False)
            network_config_file.write(yaml.dump(network_config).encode('utf-8'))
            network_config_file.close()
            genisoimage_command.append("network-config={}".format(network_config_file.name))

        genisoimage_proc = subprocess.Popen(genisoimage_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        isoimage = genisoimage_proc.stdout.read()

        debug_isoimage = False
        if debug_isoimage:
            with open('isoimage-debug.iso', 'wb') as f:
                f.write(isoimage)

        os.remove(meta_data_file.name)
        os.remove(user_data_file.name)
        if network_config:
            os.remove(network_config_file.name)

        print(f"Uploading cloud-init configuration for {user_data['hostname']}...")

        # files in the GNS3 directory take precedence over these project files,
        # so we need to make these file names unique
        cdrom_image = self.project_id + '_' + user_data['hostname'] + '.iso'
        file_url = "{}/files/{}".format(self.url, cdrom_image)
        result = requests.post(file_url, auth=self.auth, data=isoimage)
        result.raise_for_status()

        # Configure an Ubuntu cloud node

        print(f"Configuring {user_data['hostname']} node...")

        url = "{}/nodes".format(self.url)

        # It's important to use the scsi disk interface, because the IDE interface in qemu
        # has some kind of bug, probably in its handling of DISCARD operations, that
        # causes a thin provisioned disk to balloon up with garbage.
        #
        # See https://unix.stackexchange.com/questions/700050
        # and https://bugs.launchpad.net/ubuntu/+source/qemu/+bug/1974100

        ubuntu_node = {
            "compute_id": "local",
            "name": user_data['hostname'],
            "node_type": "qemu",
            "properties": {
                "adapter_type" : "virtio-net-pci",
                "hda_disk_image": image,
                "hda_disk_interface": "scsi",
                "cdrom_image" : cdrom_image,
                "qemu_path": "/usr/bin/qemu-system-x86_64",
                "process_priority": "very high",
            },

            # ens4, ens5, ens6 seems to be the numbering scheme on Ubuntu 20,
            # but we can't replicate that with a Python format string
            "port_name_format": "eth{}",

            "symbol": ":/symbols/qemu_guest.svg",
            "x" : x,
            "y" : y
        }

        if cpus:
            ubuntu_node['properties']['cpus'] = cpus
        if ram:
            ubuntu_node['properties']['ram'] = ram
        if ethernets:
            ubuntu_node['properties']['adapters'] = ethernets
        if vnc:
            ubuntu_node['console_type'] = 'vnc'

        result = requests.post(url, auth=self.auth, data=json.dumps(ubuntu_node))
        result.raise_for_status()
        ubuntu = result.json()

        if disk and disk > 2048:
            url = "{}/compute/projects/{}/qemu/nodes/{}/resize_disk".format(self.url, ubuntu['node_id'])
            resize_obj = {'drive_name' : 'hda', 'extend' : disk - 2048}
            result = requests.post(url, auth=self.auth, data=json.dumps(resize_obj))
            result.raise_for_status()

        self.nodes()  # update self.cached_nodes
        return ubuntu

    def start_ubuntu_node(self, ubuntu):

        print(f"Starting {ubuntu['name']}...")

        project_start_url = "{}/nodes/{}/start".format(self.url, ubuntu['node_id'])
        result = requests.post(project_start_url, auth=self.auth)
        result.raise_for_status()

    def create_cloud(self, name, interface, x=0, y=0):

        print(f"Configuring cloud {name} for access to interface {interface}...")

        cloud_node = {
            "compute_id": "local",
            "name": name,
            "node_type": "cloud",

            "properties" : {
                "ports_mapping": [
                    {
                        "interface": interface,
                        "name": interface,
                        "port_number": 0,
                        "type": "ethernet"
                    }
                ],
            },

            "symbol": ":/symbols/cloud.svg",
            "x" : x,
            "y" : y,
        }

        url = "{}/nodes".format(self.url)

        result = requests.post(url, auth=self.auth, data=json.dumps(cloud_node))
        result.raise_for_status()
        return result.json()

    def create_switch(self, name, ethernets=None, x=0, y=0):

        print(f"Configuring Ethernet switch {name}...")

        switch_node = {
            "compute_id": "local",
            "name": name,
            "node_type": "ethernet_switch",

            "symbol": ":/symbols/ethernet_switch.svg",
            "x" : x,
            "y" : y
        }

        if ethernets:
            switch_node['properties'] = {}
            ports = [{"name": f"Ethernet{i}", "port_number": i, "type": "access", "vlan": 1} for i in range(ethernets)]
            switch_node['properties']['ports_mapping'] = ports

        url = "{}/nodes".format(self.url)

        result = requests.post(url, auth=self.auth, data=json.dumps(switch_node))
        result.raise_for_status()
        return result.json()

    def create_link(self, node1, port1, node2, port2=None):
        r"""
        Creates a virtual network link from node1/port1 to node2/port2.

        'port2' is optional; the first available port on 'node2' will be used if it is not specified.
        """
        if not port2:
            ports_in_use = set((node['adapter_number'], node['port_number']) for link in self.links() for node in link['nodes'] if node['node_id'] == node2['node_id'])
            available_ports = (port for port in node2['ports'] if (port['adapter_number'], port['port_number']) not in ports_in_use)
            next_available_port = next(available_ports)
        else:
            next_available_port = node2['ports'][port2]

        link_obj = {'nodes' : [{'adapter_number' : node1['ports'][port1]['adapter_number'],
                                'port_number' : node1['ports'][port1]['port_number'],
                                'label' : { 'text' : node1['ports'][port1]['name']},
                                'node_id' : node1['node_id']},
                               {'adapter_number' : next_available_port['adapter_number'],
                                'port_number' : next_available_port['port_number'],
                                'label' : { 'text' : next_available_port['name']},
                                'node_id' : node2['node_id']}]}

        links_url = "{}/links".format(self.url)

        result = requests.post(links_url, auth=self.auth, data=json.dumps(link_obj))
        result.raise_for_status()
        #links.append(link_obj)

    ### DECLARE NODES: CREATE THEM, BUT ONLY IF THEY DON'T ALREADY EXIST

    def ubuntu_node(self, user_data, *args, **kwargs):
        name = user_data['hostname']
        if not self.cached_nodes:
            self.nodes()
        for node in self.cached_nodes:
            if node['name'] == name:
                return node
        return self.create_ubuntu_node(user_data, *args, **kwargs)

    def cloud(self, name, *args, **kwargs):
        if not self.cached_nodes:
            self.nodes()
        for node in self.cached_nodes:
            if node['name'] == name:
                return node
        return self.create_cloud(name, *args, **kwargs)

    def switch(self, name, *args, **kwargs):
        if not self.cached_nodes:
            self.nodes()
        for node in self.cached_nodes:
            if node['name'] == name:
                return node
        return self.create_switch(name, *args, **kwargs)

    def link(self, node1, port1, node2, port2=None):
        for link in self.links():
            if link['nodes'][0]['node_id'] == node1['node_id'] and \
               link['nodes'][0]['port_number'] == node1['ports'][port1]['port_number'] and \
               link['nodes'][0]['adapter_number'] == node1['ports'][port1]['adapter_number'] and \
               link['nodes'][1]['node_id'] == node2['node_id']:
                return
            if link['nodes'][1]['node_id'] == node1['node_id'] and \
               link['nodes'][1]['port_number'] == node1['ports'][port1]['port_number'] and \
               link['nodes'][1]['adapter_number'] == node1['ports'][port1]['adapter_number'] and \
               link['nodes'][0]['node_id'] == node2['node_id']:
                return
        self.create_link(node1, port1, node2, port2)
