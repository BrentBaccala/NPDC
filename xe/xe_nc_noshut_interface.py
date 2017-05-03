#!/usr/bin/env python

from lxml import etree
from ncclient import manager

if __name__ == "__main__":

    with manager.connect(host='52.55.197.114', port=830, username='brent', password='baccala',
                         hostkey_verify=False, device_params={'name': 'csr'},
                         allow_agent=False, look_for_keys=False) as device:


        nc_filter = """
                <config>
                <native xmlns="http://cisco.com/ns/yang/ned/ios">
                 <interface>
                  <Loopback>
                    <name>117</name>
                    <shutdown/>
                  </Loopback>
                 </interface>
                </native>
                </config>
        """

        nc_reply = device.edit_config(target='running', config=nc_filter)
        print nc_reply
        
