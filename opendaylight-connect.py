#!/usr/bin/python

import requests
from requests.auth import HTTPBasicAuth

import json

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

auth = HTTPBasicAuth('admin', 'admin')

headers = {'Content-Type': 'application/xml',
           'Accept': 'application/xml'}

# Copied from https://docs.opendaylight.org/en/stable-oxygen/user-guide/netconf-user-guide.html

url1 = 'http://blade8:8181/restconf/config/network-topology:network-topology/topology/topology-netconf/node/controller-config/yang-ext:mount/config:modules'

payload1 = '''
<module xmlns="urn:opendaylight:params:xml:ns:yang:controller:config">
  <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">prefix:sal-netconf-connector</type>
  <name>new-netconf-device</name>
  <address xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">127.0.0.1</address>
  <port xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">830</port>
  <username xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">admin</username>
  <password xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">admin</password>
  <tcp-only xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">false</tcp-only>
  <event-executor xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:netty">prefix:netty-event-executor</type>
    <name>global-event-executor</name>
  </event-executor>
  <binding-registry xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:md:sal:binding">prefix:binding-broker-osgi-registry</type>
    <name>binding-osgi-broker</name>
  </binding-registry>
  <dom-registry xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:md:sal:dom">prefix:dom-broker-osgi-registry</type>
    <name>dom-broker</name>
  </dom-registry>
  <client-dispatcher xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:config:netconf">prefix:netconf-client-dispatcher</type>
    <name>global-netconf-dispatcher</name>
  </client-dispatcher>
  <processing-executor xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:threadpool">prefix:threadpool</type>
    <name>global-netconf-processing-executor</name>
  </processing-executor>
  <keepalive-executor xmlns="urn:opendaylight:params:xml:ns:yang:controller:md:sal:connector:netconf">
    <type xmlns:prefix="urn:opendaylight:params:xml:ns:yang:controller:threadpool">prefix:scheduled-threadpool</type>
    <name>global-netconf-ssh-scheduled-executor</name>
  </keepalive-executor>
</module>
'''

url2 = "http://blade8:8181/restconf/config/network-topology:network-topology/topology/topology-netconf/node/csr3"

payload2 = '''
<node xmlns="urn:TBD:params:xml:ns:yang:network-topology">
  <node-id>csr3</node-id>
  <host xmlns="urn:opendaylight:netconf-node-topology">192.168.58.124</host>
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

response = requests.put(url2, data=payload2, verify=False, auth=auth, headers=headers)

print(response.text)
