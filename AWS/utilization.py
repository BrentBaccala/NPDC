#!/usr/bin/python
#
# Sometimes I'd like to see a quick graph of EC2 CPU utilization
# without having to login to the console.
#
# This script does that.  It passes its switches on the various aws
# commands, so it can be run like "utilization.py kyle".
#
# You probably need to run this on Linux, as it uses the Go program
# chart for its graphical output.

import boto3
import sys
from datetime import datetime
from datetime import timedelta
import subprocess

# Use the first argument to the script as the name of the AWS profile

session=boto3.Session(profile_name=sys.argv[1])

cloudwatch = session.client('cloudwatch')

# AWS EC2 collects a number of statistics by default.
#
# We can get a list of them like this:

metrics = cloudwatch.list_metrics()['Metrics']

for m in metrics:
  print m['MetricName']

metricname = "CPUUtilization"
#metricname="Requests"

# We need to pick a statistic to aggregate the data.  I couldn't find
# any way to retreive the raw data without aggregation.

stat = "Maximum"
#stat = "Sum"
#stat = "SampleCount"

for m in metrics:
    if m['MetricName'] == metricname:

       # We might have multiple entries in 'metrics' with the same
       # metric name.  They correspond to different "dimensions",
       # things like different instances for collecting CPU
       # utiliation.

       # We'll produce one graph for each available metric

       stats = cloudwatch.get_metric_statistics(Namespace=m['Namespace'], MetricName=metricname,
						Dimensions=m['Dimensions'],
						StartTime=datetime.now() - timedelta(days=1),
						EndTime=datetime.now(),
						Period=60,
						Statistics=[stat])

       stats['Datapoints'].sort(key=lambda p: p['Timestamp'])

       # Pipe the collected data to the 'chart' program, which will
       # create an HTML file in /tmp and display it in your web
       # browser.

       title = " ".join([d["Value"] for d in m['Dimensions']]) + " " + metricname
       chart = subprocess.Popen(["/home/baccala/go/bin/chart", "-t", title, "line", " "], stdin=subprocess.PIPE)

       for datapoint in stats['Datapoints']:
	 print >>chart.stdin, datapoint['Timestamp'].isoformat(), datapoint[stat]

       chart.stdin.close()
