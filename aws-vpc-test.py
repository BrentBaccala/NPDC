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

def delete_extraneous_vpcs():

  for subnet in ec2.describe_subnets()['Subnets']:
    if subnet['CidrBlock'] == '10.0.0.0/16':
      print subnet
      ec2.delete_subnet(SubnetId=subnet['SubnetId'])

  for vpc in ec2.describe_vpcs()['Vpcs']:
    if vpc['CidrBlock'] == '10.0.0.0/16':
       print vpc
       ec2.delete_vpc(VpcId=vpc['VpcId'])

def create_two_instances():
  response = ec2.run_instances(
          ImageId='ami-f4cc1de2',
          MinCount=2,
          MaxCount=2,
          InstanceType='t2.medium',
          SubnetId = SubnetId)

vpcids=[]
for vpc in ec2.describe_vpcs()['Vpcs']:
  if vpc['CidrBlock'] == '10.0.0.0/16':
     vpcids.append(vpc['VpcId'])

print vpcids

response = ec2.describe_instances()
for resv in response['Reservations']:
  for instance in resv['Instances']:
    if instance.get('VpcId') in vpcids:
      #print instance
      #ec2.terminate_instances(InstanceIds=[instance['InstanceId']])
      print instance['InstanceId'], instance['State']

delete_extraneous_vpcs()

#print ec2.delete_subnet(SubnetId=SubnetId)

#print ec2.delete_vpc(VpcId=VpcId)

