#!/bin/bash
#
# restore-collaborate-snapshot.sh — roll collaborate.freesoft.org's root disk
# back to a snapshot, IN PLACE (same instance ID), so the wake Lambda, the
# InstanceId-pinned hibernate credential, and the self-updating Route53 record
# all keep working. Do NOT "launch a new instance from an AMI" — that gets a new
# instance ID and breaks all of collaborate's plumbing (see
# ~/project/docs/bbb-freesoft-infrastructure.md § Snapshots / rollback).
#
# Usage:  restore-collaborate-snapshot.sh <snapshot-id> [--yes]
# Example: restore-collaborate-snapshot.sh snap-0509cf45dca8e8338
#
# Needs admin creds (the mutating volume ops are outside the scoped `claude`
# role): run with --profile bruce, i.e. PROFILE=bruce below.
#
set -euo pipefail

SNAP="${1:-}"
CONFIRM="${2:-}"
PROFILE="${PROFILE:-bruce}"
REGION="us-east-1"
AZ="us-east-1a"
INSTANCE="i-08855473c85a62721"
ROOT_DEV="/dev/sda1"

if [[ -z "$SNAP" ]]; then
  echo "usage: $0 <snapshot-id> [--yes]" >&2
  exit 2
fi

aws() { command aws --profile="$PROFILE" --region="$REGION" "$@"; }

echo "About to restore $INSTANCE root ($ROOT_DEV) from $SNAP in $AZ."
echo "The current root volume will be DETACHED and kept (not deleted) as a fallback."
if [[ "$CONFIRM" != "--yes" ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }
fi

echo "==> 1/6 creating volume from $SNAP (inherits the snapshot's encryption)"
NEW_VOL=$(aws ec2 create-volume --availability-zone "$AZ" --snapshot-id "$SNAP" \
  --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=collaborate-restore-$SNAP},{Key=owner,Value=claude}]" \
  --query VolumeId --output text)
echo "    new volume: $NEW_VOL"
aws ec2 wait volume-available --volume-ids "$NEW_VOL"

echo "==> 2/6 stopping instance (full stop, not hibernate)"
aws ec2 stop-instances --instance-ids "$INSTANCE" >/dev/null
aws ec2 wait instance-stopped --instance-ids "$INSTANCE"

echo "==> 3/6 finding & detaching current root volume"
CUR_ROOT=$(aws ec2 describe-instances --instance-ids "$INSTANCE" \
  --query "Reservations[].Instances[].BlockDeviceMappings[?DeviceName=='$ROOT_DEV'].Ebs.VolumeId" \
  --output text)
echo "    current root: $CUR_ROOT (will be detached & kept)"
aws ec2 detach-volume --volume-id "$CUR_ROOT" >/dev/null
aws ec2 wait volume-available --volume-ids "$CUR_ROOT"

echo "==> 4/6 attaching restored volume as $ROOT_DEV"
aws ec2 attach-volume --instance-id "$INSTANCE" --volume-id "$NEW_VOL" --device "$ROOT_DEV" >/dev/null
aws ec2 wait volume-in-use --volume-ids "$NEW_VOL"

echo "==> 5/6 starting instance"
aws ec2 start-instances --instance-ids "$INSTANCE" >/dev/null
aws ec2 wait instance-running --instance-ids "$INSTANCE"

echo "==> 6/6 done."
echo "    restored root : $NEW_VOL (attached as $ROOT_DEV)"
echo "    old root kept  : $CUR_ROOT  (delete once you've confirmed the restore is good:"
echo "                     aws --profile=$PROFILE --region=$REGION ec2 delete-volume --volume-id $CUR_ROOT )"
echo
echo "The box does a COLD BOOT from the restored disk (this was a full stop, not a"
echo "hibernate-resume). ddclient will UPSERT the Route53 A record within ~60s;"
echo "give DNS a minute before hitting https://collaborate.freesoft.org/."
