#!/usr/bin/python
#
# Connect csr3 (which should be listening for NETCONF calls) to OpenDaylight on blade8

import requests
from requests.auth import HTTPBasicAuth

import json

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

auth = HTTPBasicAuth('admin', 'admin')

headers = {'Content-Type': 'application/xml',
           'Accept': 'application/xml'}

# Copied from https://docs.opendaylight.org/en/stable-oxygen/user-guide/netconf-user-guide.html

nodes = {'csr4' : {'node' : 'csr4', 'host': '192.168.58.130'}}

url2 = "http://blade8:8181/restconf/config/network-topology:network-topology/topology/topology-netconf/node/{}"

payload2 = '''
<node xmlns="urn:TBD:params:xml:ns:yang:network-topology">
  <node-id>{node}</node-id>
  <host xmlns="urn:opendaylight:netconf-node-topology">{host}</host>
  <port xmlns="urn:opendaylight:netconf-node-topology">830</port>
  <username xmlns="urn:opendaylight:netconf-node-topology">admin</username>
  <password xmlns="urn:opendaylight:netconf-node-topology">admin</password>
  <tcp-only xmlns="urn:opendaylight:netconf-node-topology">false</tcp-only>
  <!-- non-mandatory fields with default values, you can safely remove these if you do not wish to override any of these values-->
  <reconnect-on-changed-schema xmlns="urn:opendaylight:netconf-node-topology">false</reconnect-on-changed-schema>
  <connection-timeout-millis xmlns="urn:opendaylight:netconf-node-topology">20000</connection-timeout-millis>
  <max-connection-attempts xmlns="urn:opendaylight:netconf-node-topology">0</max-connection-attempts>
  <between-attempts-timeout-millis xmlns="urn:opendaylight:netconf-node-topology">2000</between-attempts-timeout-millis>
  <sleep-factor xmlns="urn:opendaylight:netconf-node-topology">1.5</sleep-factor>
  <!-- keepalive-delay set to 0 turns off keepalives-->
  <keepalive-delay xmlns="urn:opendaylight:netconf-node-topology">120</keepalive-delay>
</node>
'''

for k,v in nodes.items():
    response = requests.put(url2.format(k), data=payload2.format(**v), verify=False, auth=auth, headers=headers)

print(response.text)
