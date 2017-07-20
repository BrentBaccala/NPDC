#!/usr/bin/python

import boto3

session = boto3.Session(profile_name='bruce')

ec2 = session.client('ec2')

# response = ec2.describe_instances()
# print(response)
# print [[i['InstanceId'], i['State']] for r in response['Reservations'] for i in r['Instances']]

def create_vpc_and_subnet():

  response = ec2.create_vpc(CidrBlock='10.0.0.0/16')
  VpcId = response['Vpc']['VpcId']

  print 'VpcId:', VpcId

  response = ec2.create_subnet(VpcId=VpcId, CidrBlock='10.0.0.0/16')

  SubnetId = response['Subnet']['SubnetId']

  print 'SubnetId:', SubnetId

  return SubnetId

def create_two_instances(SubnetId):
  response = ec2.run_instances(
          ImageId='ami-f4cc1de2',
          MinCount=2,
          MaxCount=2,
          InstanceType='t2.medium',
          KeyName='baccala',
          SubnetId = SubnetId)

def get_vpcids():
  vpcids=[]
  for vpc in ec2.describe_vpcs()['Vpcs']:
    if vpc['CidrBlock'] == '10.0.0.0/16':
       vpcids.append(vpc['VpcId'])
  return vpcids

#print vpcids

def get_instances(vpcids):
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
  ec2.associate_address(AllocationId = find_first_unassociated_ip(), InstanceId = get_instances(get_vpcids())[0])
  

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

  for subnet in ec2.describe_subnets()['Subnets']:
    if subnet['CidrBlock'] == '10.0.0.0/16':
      print 'Deleting Subnet:', subnet
      ec2.delete_subnet(SubnetId=subnet['SubnetId'])

  for vpc in ec2.describe_vpcs()['Vpcs']:
    if vpc['CidrBlock'] == '10.0.0.0/16':
       print 'Deleting VPC:', vpc
       ec2.delete_vpc(VpcId=vpc['VpcId'])

def print_status():
  vpcids = get_vpcids()
  print "vpcids = ", vpcids
  for gw in ec2.describe_internet_gateways()['InternetGateways']:
    if gw['Attachments'][0]['VpcId'] in vpcids:
      print 'Gateway: ', gw
  for sn in ec2.describe_subnets()['Subnets']:
    if sn['VpcId'] in vpcids:
      print 'Subnet:', sn
  print 'Instances:', get_instances(vpcids)

# This creates a default route to the Internet Gateway

#ec2.create_route(RouteTableId='rtb-08113a70', DestinationCidrBlock='0.0.0.0/0', GatewayId='igw-c4dc94a2')

#delete_extraneous_vpcs()

#print ec2.delete_subnet(SubnetId=SubnetId)

#print ec2.delete_vpc(VpcId=VpcId)

#ec2.create_internet_gateway()
#ec2.attach_internet_gateway(InternetGatewayId='igw-c4dc94a2', VpcId=get_vpcids()[0])

