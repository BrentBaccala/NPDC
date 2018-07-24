#!/usr/bin/python

import requests
from lxml import etree
from ucsmsdk.ucshandle import UcsHandle
from ucscsdk.ucschandle import UcscHandle

url = 'http://ucspe/nuova'
xmlIM = 'https://192.168.58.100/xmlIM/central-mgr/'
ucsCentral = '192.168.58.100'
service = 'ucspe'
user = 'ucspe'
pwd = 'ucspe'
target_dn = 'sys/chassis-4/psu-1'


def usingRawPython():
    cookie_request = '<aaaLogin inName="{}" inPassword="{}"/>'.format(user,pwd)
    response = requests.post(url, data=cookie_request, verify=False)
    cookie = etree.fromstring(response.text).attrib['outCookie']

    getPsuInfo = '<configResolveDn cookie="{}" inHierarchical="false" dn="{}"/>'.format(cookie,target_dn)
    response = requests.post(url, data=getPsuInfo, verify=False)
    psu_xml = etree.fromstring(response.text)
    print "Model #:", psu_xml.find('.//equipmentPsu').attrib['model']
    

def usingUcsmsdk():
    handle = UcsHandle(service, user, pwd)
    handle.login()

    psu = handle.query_dn(target_dn)
    print 'Model #:', psu.model


def usingUcscsdk():
    handle = UcscHandle(ucsCentral, 'admin', 'cisco123')
    handle.login()

    mac_pool = handle.query_dn('org-root/mac-pool-global-default')
    print 'Name:', mac_pool.name



usingRawPython()
usingUcsmsdk()
usingUcscsdk()

