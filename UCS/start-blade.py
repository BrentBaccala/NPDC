#!/usr/bin/python
#
# Script to start a UCS blade.  First queries the service profile to
# see if the blade is running.  If so, do nothing.  If not, modify its
# power object to boot it.
#
# This didn't produce anything useful...
#
#     ucsguilaunch.ucs_gui_launch(handle)
#     converttopython.convert_to_ucs_python()
#
# Instead, I got the answer I wanted from here:
#   https://communities.cisco.com/thread/84717

import sys

from getpass import *
from ucsmsdk import *
from ucsmsdk.ucshandle import *

from ucsmsdk.utils import ucsguilaunch
from ucsmsdk.utils import converttopython


# We expect the user to specify the blade number as an argument,
# according to our local service profile naming scheme.

if len(sys.argv) == 2:
    dn1 = 'org-root/ls-Local0{}'.format(sys.argv[1])
else:
    print("Which blade do you wish to start?")
    exit()

password = getpass('UCS Password: ')

handle = UcsHandle('172.18.0.100', 'admin', password)

handle.login()

mo1 = handle.query_dn(dn1)

print dn1, "oper_state is", mo1.oper_state



if mo1.oper_state == 'ok':

    print 'doing nothing'

else:

    # The main options are 'up' or maybe 'admin-up', and 'soft-shut-down', or 'down'.
    #
    # 'up' doesn't seem to boot the server; you need to use 'admin-up'

    dn2 = dn1 + '/power'

    mo2 = handle.query_dn(dn2)

    print 'setting {} state to admin-up'.format(dn2)

    mo2.state = 'admin-up'

    handle.set_mo(mo2)

    handle.commit()
