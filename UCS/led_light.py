#!/usr/bin/python

import sys
from ucsmsdk.ucshandle import UcsHandle

handle = UcsHandle(ip='172.18.0.100', username='admin', password='cisco123')

handle.login()

# 'shortnames' maps short names to full DNs
#
# The short names are 'chassis', 'switch', and the integers 1 through 8 (for the blades)

shortnames = {'chassis': 'sys/chassis-1/locator-led',
              'switch': 'sys/switch-A/locator-led'}

shortnames.update({str(i): 'sys/chassis-1/blade-{}/locator-led'.format(i) for i in range(1,9)})

# If two arguments were given to the script, it's the name of the object we want to
# operate on, plus the operations ('on' or 'off')
#
# Otherwise, we print the status of all the locator LEDs

if len(sys.argv) == 3:

  led = handle.query_dn(shortnames.get(sys.argv[1], sys.argv[1]))
  led.admin_state = sys.argv[2]
  handle.set_mo(led)
  handle.commit()

else:

  result = handle.query_classid('EquipmentLocatorLed')

  for i in sorted(result, key=lambda led: led.dn):
    print i.dn, "is", i.oper_state
