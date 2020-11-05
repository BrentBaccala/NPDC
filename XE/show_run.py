#!/usr/bin/env python3

import sys
import code
from lxml import etree
from ncclient import manager



if __name__ == "__main__":

    with manager.connect(host=sys.argv[1], port=830, username='admin', password='admin',
                         hostkey_verify=False, device_params={'name': 'csr'},
                         allow_agent=False, look_for_keys=False) as device:


        nc_filter = """
                <config>
                </config>
        """

        for cap in device.server_capabilities:
            print(cap)

        nc_reply = device.get_config('running')
        # print nc_reply
        print(etree.tostring(nc_reply.data_ele, pretty_print=True))

        code.interact(None, None, locals())
