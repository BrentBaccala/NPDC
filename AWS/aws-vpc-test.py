#!/usr/bin/python

import boto3
import time

try:
   session = boto3.Session()
   ec2 = session.client('ec2')
except:
   session = boto3.Session(profile_name='vae')
   ec2 = session.client('ec2')

# response = ec2.describe_instances()
# print(response)
# print [[i['InstanceId'], i['State']] for r in response['Reservations'] for i in r['Instances']]

VpcId = ''

def import_key_pair():
   file = open('/home/baccala/.ssh/id_rsa.pub')
   key = file.read()
   ec2.import_key_pair(KeyName='baccala', PublicKeyMaterial=key)


def create_vpc():

  global VpcId

  response = ec2.create_vpc(CidrBlock='10.0.0.0/16')
  VpcId = response['Vpc']['VpcId']

  print 'VpcId:', VpcId

  # Create an Internet gateway (IGW)

  gateway = ec2.create_internet_gateway()['InternetGateway']['InternetGatewayId']
  ec2.attach_internet_gateway(InternetGatewayId = gateway, VpcId = VpcId)

  print 'Gateway:', gateway

  # This creates a default route to the Internet Gateway

  rtid = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [VpcId]}])['RouteTables'][0]['RouteTableId']
  ec2.create_route(RouteTableId = rtid, DestinationCidrBlock = '0.0.0.0/0', GatewayId = gateway)

  print 'Route Table:', rtid

  # This authorizes all inbound traffic

  for sg in ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [VpcId]}])['SecurityGroups']:
    ec2.authorize_security_group_ingress(GroupId=sg['GroupId'], CidrIp='0.0.0.0/0', IpProtocol='-1')


def get_vpcids():
  vpcids=[]
  global VpcId
  for vpc in ec2.describe_vpcs()['Vpcs']:
    if vpc['CidrBlock'] == '10.0.0.0/16':
       vpcids.append(vpc['VpcId'])
  if len(vpcids) > 0: VpcId = vpcids[0]
  return vpcids


def print_sgs():
  for sg in ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': get_vpcids()}])['SecurityGroups']:
    print sg


def create_one_subnet():

  response = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.0.0/16')

  SubnetId = response['Subnet']['SubnetId']

  print 'SubnetId:', SubnetId

  return SubnetId

def create_two_subnets():

  response1 = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.1.0/24')
  print response1

  response2 = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.2.0/24')
  print response2

  return [response1['Subnet']['SubnetId'], response2['Subnet']['SubnetId']]

# default ami (f4cc1de2) is Ubuntu Linux

instance_type={'ami-e0e0adf7':'c4.large'}    # e0e0adf7 is Cisco ASA

def create_instances(N, SubnetId, AMI='ami-f4cc1de2', InstanceType=None, Name=None, UserData=None, Hibernate=None):
   params = {
      'ImageId' : AMI,
      'MinCount' : N,
      'MaxCount' : N,
      'KeyName' : 'BruceCaslow@itpietraining',
      'SubnetId' : SubnetId
   }

   if InstanceType:
      params['InstanceType'] = InstanceType
   else:
      params['InstanceType'] = instance_type.get(AMI, 't2.medium')

   if Name:
      params['TagSpecifications'] = [{'ResourceType' : 'instance', 'Tags' : [{'Key' : 'Name', 'Value' : Name }]}]

   if UserData:
      params['UserData'] = UserData

   if Hibernate:
      params['BlockDeviceMappings'] = [{'DeviceName' : '/dev/sda1', 'Ebs' : {'VolumeSize': Hibernate, 'Encrypted' : True}}]
      params['HibernationOptions'] = {'Configured' : True}

   response = ec2.run_instances(**params)

   return [inst['InstanceId'] for inst in response['Instances']]

   # List comprehension: the last line has the same result as this:
   #
   # result=[]
   # for inst in response['Instances']
   #   result.append(inst['InstanceId'])
   # return result

def create_two_armed(mode='original'):

  if mode=='centos':
    ami1='ami-46c1b650'
    ami2='ami-d5fa95c3'  # CSR 1000V AX
    CiscoCommands = ['interface G 2', 'ip address dhcp', 'no shut', 'exit', 'hostname Brent', 'router ospf 171']
  elif mode=='asav':
    ami1='ami-46c1b650'
    ami2='ami-e0e0adf7'  # Cisco ASA
    CiscoCommands = []
  elif mode=='original':
    ami1='ami-f4cc1de2'
    ami2='ami-23f79835'   # CSR 1000V BYOL
    CiscoCommands = ['interface G 2', 'ip address dhcp', 'no shut', 'exit', 'hostname Brent', 'router ospf 171']
  else:
    print 'Unknown mode'
    return

  CiscoUserData = '\n'.join(['ios-config-{}="{}"'.format(*pair) for pair in enumerate(CiscoCommands, 1)])

  try:
    subnets = create_two_subnets()
  except:
    subnets = get_subnets()

  instance1 = create_instances(1, subnets[0],AMI=ami1)[0]
  instance2 = create_instances(1, subnets[1],AMI=ami1)[0]
  cisco = create_instances(1, subnets[0], AMI=ami2, UserData=CiscoUserData)[0]

  while ec2.describe_instances(InstanceIds=[cisco])['Reservations'][0]['Instances'][0]['State']['Name'] != 'running':
    print 'Waiting for', cisco, 'to start'
    time.sleep(3)

  original_nid = find_network_interfaces(cisco)[0]

  # attach a second network interface to the CSR 1000V

  nid = ec2.create_network_interface(SubnetId=subnets[1])['NetworkInterface']['NetworkInterfaceId']
  ec2.attach_network_interface(NetworkInterfaceId = nid, InstanceId = cisco, DeviceIndex=1)

  # associate our elastic IP address with the CSR 1000V's original network interface

  associate_elastic_ip(NetworkInterface = original_nid)

def get_instances(vpcids = get_vpcids()):
  response = ec2.describe_instances()
  result = []
  for resv in response['Reservations']:
    for instance in resv['Instances']:
      if instance.get('VpcId') in vpcids:
        #print instance
        #ec2.terminate_instances(InstanceIds=[instance['InstanceId']])
        # print instance['InstanceId'], instance['State']
        result.append(instance['InstanceId'])
  return result

def get_stopped_instances(vpcids = get_vpcids()):
  response = ec2.describe_instances()
  result = []
  for resv in response['Reservations']:
    for instance in resv['Instances']:
      if instance.get('VpcId') in vpcids:
        if instance['State']['Code']==80:
            result.append(instance['InstanceId'])
  return result

def find_network_interfaces(instance):
  return [ni['NetworkInterfaceId'] for ni in ec2.describe_network_interfaces(Filters=[{'Name': 'attachment.instance-id', 'Values': [instance]}])['NetworkInterfaces']]

#### ELASTIC IP ADDRESES

def find_first_unassociated_ip():
  for addr in ec2.describe_addresses()['Addresses']:
    if 'InstanceId' not in addr:
      return addr['AllocationId']
  return ec2.allocate_address(Domain='vpc')['AllocationId']

def associate_elastic_ip(InstanceId = None, NetworkInterface = None):
  # get_instances()[0]
  if NetworkInterface == None:
    ec2.associate_address(AllocationId = find_first_unassociated_ip(), InstanceId = InstanceId)
  else:
    ec2.associate_address(AllocationId = find_first_unassociated_ip(), NetworkInterfaceId = NetworkInterface)

def find_elastic_ip():
  for addr in ec2.describe_addresses()['Addresses']:
    ipaddr = addr.get('PrivateIpAddress')
    if type(ipaddr) == type('string') and ipaddr.startswith('10.0.'):
#      return addr['AllocationId']
      return addr['AssociationId']

def disassociate_elastic_ip():
  ec2.disassociate_address(AssociationId = find_elastic_ip())  

def terminate_instances():
  for i in get_instances(get_vpcids()):
    ec2.terminate_instances(InstanceIds=[i])

def get_subnets():
  vpcids = get_vpcids()
  subnets=[]
  for sn in ec2.describe_subnets()['Subnets']:
    if sn['VpcId'] in vpcids:
      subnets.append(sn['SubnetId'])
  return subnets

def delete_subnets():

  subnets = get_subnets()

  for ni in ec2.describe_network_interfaces(Filters=[{'Name': 'subnet-id', 'Values': subnets}])['NetworkInterfaces']:
    print 'Deleting Network Interface:', ni['NetworkInterfaceId']
    ec2.delete_network_interface(NetworkInterfaceId = ni['NetworkInterfaceId'])

  for subnet in subnets:
    print 'Deleting Subnet:', subnet
    ec2.delete_subnet(SubnetId=subnet)


def delete_extraneous_vpcs():

  vpcids = get_vpcids()

  for gw in ec2.describe_internet_gateways()['InternetGateways']:
    if gw['Attachments'][0]['VpcId'] in vpcids:
      print 'Deleting Gateway: ', gw
      ec2.detach_internet_gateway(InternetGatewayId = gw['InternetGatewayId'], VpcId = gw['Attachments'][0]['VpcId'])
      ec2.delete_internet_gateway(InternetGatewayId = gw['InternetGatewayId'])

  delete_subnets()

  for vpc in vpcids:
     print 'Deleting VPC:', vpc
     ec2.delete_vpc(VpcId=vpc)

  # for vpc in ec2.describe_vpcs()['Vpcs']:
  #   if vpc['CidrBlock'] == '10.0.0.0/16':
  #      print 'Deleting VPC:', vpc
  #      ec2.delete_vpc(VpcId=vpc['VpcId'])

def print_status():
  vpcids = get_vpcids()
  print "vpcids = ", vpcids
  print "VpcId = ", VpcId
  for gw in ec2.describe_internet_gateways()['InternetGateways']:
    if gw['Attachments'][0]['VpcId'] in vpcids:
      print 'Gateway: ', gw
  subnets=[]
  for sn in ec2.describe_subnets()['Subnets']:
    if sn['VpcId'] in vpcids:
      print 'Subnet:', sn
      subnets.append(sn['SubnetId'])
  print 'Instances:', get_instances(vpcids)
  return subnets


# ec2.create_network_interface(SubnetId=sns[1])
# ec2.attach_network_interface(NetworkInterfaceId='eni-d0ee3302', InstanceId='i-09ee721db82537f20', DeviceIndex=1)

def start_instances():
  for i in get_stopped_instances(get_vpcids()):
    ec2.start_instances(InstanceIds=[i])

def stop_instances():
  for i in get_instances(get_vpcids()):
    ec2.stop_instances(InstanceIds=[i])



def create_bbb_server():

  try:
    subnets = create_one_subnet()
  except:
    subnets = get_subnets()

  # http://cloud-images.ubuntu.com/locator/ec2/
  # gives ami-09d160e4b73e3e8ac for us-east-2, bionic, amd, ebs

  ami = "ami-09d160e4b73e3e8ac"

  instance = create_instances(1, subnets[0], AMI=ami, InstanceType='c5.4xlarge', Name='itpietraining.com', Hibernate=64)[0]

  original_nid = find_network_interfaces(instance)[0]

  # XXX this call has to wait until the instance is in a valid state for it
  ec2.associate_address(AllocationId = 'eipalloc-004adb1fe297bf793', NetworkInterfaceId = original_nid)


def create_turn_server():

  try:
    subnets = create_one_subnet()
  except:
    subnets = get_subnets()

  # http://cloud-images.ubuntu.com/locator/ec2/
  # gives ami-09d160e4b73e3e8ac for us-east-2, bionic, amd, ebs

  ami = "ami-09d160e4b73e3e8ac"

  instance = create_instances(1, subnets[0], AMI=ami, InstanceType='t3a.micro', Name='turn.itpietraining.com')[0]

  original_nid = find_network_interfaces(instance)[0]

  # XXX this call has to wait until the instance is in a valid state for it
  ec2.associate_address(AllocationId = 'eipalloc-05ce119ed906a0b8a', NetworkInterfaceId = original_nid)

def create_itpie_server():

  try:
    subnets = create_one_subnet()
  except:
    subnets = get_subnets()

  # https://wiki.centos.org/Cloud/AWS
  # gives AMI for us-east-2, CentOS Linux 7.8.2003

  ami = "ami-0a75b786d9a7f8144"

  instance = create_instances(1, subnets[0], AMI=ami, InstanceType='c5.xlarge', Name='itpie.itpietraining.com')[0]

  original_nid = find_network_interfaces(instance)[0]

  print(original_nid)

  # XXX this call has to wait until the instance is in a valid state for it
  ec2.associate_address(AllocationId = 'eipalloc-0cb6bf6b491b9426e', NetworkInterfaceId = original_nid)


def create_Cisco_XR():

  try:
    subnets = create_one_subnet()
  except:
    subnets = get_subnets()

  ami = "ami-534a6436"

  instance = create_instances(1, subnets[0], AMI=ami, InstanceType='m4.large', Name='Cisco XR')[0]
