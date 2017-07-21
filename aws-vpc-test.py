#!/usr/bin/python

import boto3

session = boto3.Session(profile_name='bruce')

ec2 = session.client('ec2')

# response = ec2.describe_instances()
# print(response)
# print [[i['InstanceId'], i['State']] for r in response['Reservations'] for i in r['Instances']]

VpcId = ''

def create_vpc():

  global VpcId

  response = ec2.create_vpc(CidrBlock='10.0.0.0/16')
  VpcId = response['Vpc']['VpcId']

  print 'VpcId:', VpcId

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

  response = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.1.0/24')
  print response

  response = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.2.0/24')
  print response

def create_instances(N, SubnetId):
  response = ec2.run_instances(
          ImageId='ami-f4cc1de2',
          MinCount=N,
          MaxCount=N,
          InstanceType='t2.medium',
          KeyName='baccala',
          SubnetId = SubnetId)

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

def find_first_unassociated_ip():
  for addr in ec2.describe_addresses()['Addresses']:
    if 'InstanceId' not in addr:
      return addr['AllocationId']

# print find_first_unassociated_ip()

def associate_elastic_ip():
  ec2.associate_address(AllocationId = find_first_unassociated_ip(), InstanceId = get_instances()[0])
  

def terminate_instances():
  for i in get_instances(get_vpcids()):
    ec2.terminate_instances(InstanceIds=[i])

def delete_extraneous_vpcs():

  vpcids = get_vpcids()

  for gw in ec2.describe_internet_gateways()['InternetGateways']:
    if gw['Attachments'][0]['VpcId'] in vpcids:
      print 'Deleting Gateway: ', gw
      ec2.detach_internet_gateway(InternetGatewayId = gw['InternetGatewayId'], VpcId = gw['Attachments'][0]['VpcId'])
      ec2.delete_internet_gateway(InternetGatewayId = gw['InternetGatewayId'])

  subnets=[]
  for sn in ec2.describe_subnets()['Subnets']:
    if sn['VpcId'] in vpcids:
      subnets.append(sn['SubnetId'])

  for ni in ec2.describe_network_interfaces(Filters=[{'Name': 'subnet-id', 'Values': subnets}])['NetworkInterfaces']:
    print 'Deleting Network Interface:', ni['NetworkInterfaceId']
    ec2.delete_network_interface(NetworkInterfaceId = ni['NetworkInterfaceId'])

  for subnet in subnets:
    print 'Deleting Subnet:', subnet
    ec2.delete_subnet(SubnetId=subnet)

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
