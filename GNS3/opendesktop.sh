#!/bin/bash
#
# This is a per-once script, i.e, it doesn't run after the first time the VM boots.

# Speed up zero'ing inode tables, because I want to wait for this to finish before
# creating the appliance, due to a probable kernel bug that fills the empty disk
# blocks with trash.

sudo mount -o remount,init_itable=0 /

# I don't do this apt stuff with cloud-init because it waits for the packages to be installed before
# running per-once scripts or even phone_home notifying.
#
# The environment is preserved with -E to pass along http_proxy, if it is set

sudo -E apt update
sudo -E DEBIAN_FRONTEND=noninteractive apt -y upgrade

# This will install the GNOME desktop so that it automatically logs in the user 'ubuntu'

sudo -E DEBIAN_FRONTEND=noninteractive apt -y install ubuntu-desktop
sudo sed -i -e 's/#  Automatic/Automatic/' -e '/Automatic/s/user1/ubuntu/' /etc/gdm3/custom.conf

# This will auto-start a terminal

mkdir -p /home/ubuntu/.config/autostart
ln -s /usr/share/applications/org.gnome.Terminal.desktop /home/ubuntu/.config/autostart

# Configure dconf to disable screen lock
sudo mkdir -p /etc/dconf/profile/
sudo tee /etc/dconf/profile/user <<EOF
user-db:user
system-db:local
EOF

sudo mkdir -p /etc/dconf/db/local.d/
sudo tee /etc/dconf/db/local.d/10disable-lock <<EOF
[org/gnome/desktop/session]
idle-delay=uint32 0
EOF

sudo dconf update

# Don't run initial user setup dialog; don't GUI prompt for updates
sudo apt -y remove update-manager gnome-initial-setup

sudo systemctl restart gdm3

# remove this installation script
sudo rm /boot.sh

uptime
