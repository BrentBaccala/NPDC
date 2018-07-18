#!/usr/bin/python

from getpass import *
from ucsmsdk import *
from ucsmsdk.ucshandle import *

from ucsmsdk.utils import ucsguilaunch
from ucsmsdk.utils import converttopython


password = getpass('UCS Password: ')

handle = UcsHandle('172.18.0.100', 'admin', password)

handle.login()

# This didn't produce anything useful...

# ucsguilaunch.ucs_gui_launch(handle)
# converttopython.convert_to_ucs_python()

# I got the answer I wanted from here:
#   https://communities.cisco.com/thread/84717

mo = handle.query_dn('org-root/ls-iSCSI07/power')

# the main options are 'up' or maybe 'admin-up', and 'soft-shut-down', or 'down'

mo.state = 'up'

handle.set_mo(mo)

handle.commit()
