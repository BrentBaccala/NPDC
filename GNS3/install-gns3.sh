#!/bin/sh
#
# This will prompt interactively several times:
#    - to confirm adding the repository
#    - to confirm installing the packages
#    - to ask if non-superusers should be able to run gns3 (say yes)
#
# SUBNET=192.168.8.0/24
#
# Environment variables can be used to silence these prompts:
#
# YES=-y will stop prompts #1 and #2
# DEBIAN_FRONTEND=noninteractive will stop prompt #3

# This is where I'd really like to use Python
# Can't currently handle anything but a /24, due to 255.255.255.0 later in the script
# maybe shell tools prips or ipcalc

SUBNET="${SUBNET:-192.168.8.0/24}"

MASKLEN=$(echo $SUBNET | cut -d / -f 2)

SUBNET_PREFIX=$(echo $SUBNET | cut -d . -f 1-3)

ZERO_HOST=$SUBNET_PREFIX.0
FIRST_HOST=$SUBNET_PREFIX.1
BROADCAST=$SUBNET_PREFIX.255
FIRST_DHCP=$SUBNET_PREFIX.129
LAST_DHCP=$SUBNET_PREFIX.199

sudo -E add-apt-repository $YES ppa:gns3

sudo -E apt $YES install gns3-server makepasswd isc-dhcp-server iptables-persistent

# should this be a --system user?
sudo adduser --gecos "GNS3 server" --disabled-password gns3

sudo adduser gns3 kvm
sudo adduser gns3 ubridge

sudo loginctl enable-linger gns3

echo 'export XDG_RUNTIME_DIR=/run/user/$(id -u)' | sudo su gns3 -c "cat >>/home/gns3/.bashrc"

sudo su gns3 -c "mkdir -p /home/gns3/.config/systemd/user"
sudo su gns3 -c "cat >/home/gns3/.config/systemd/user/gns3.service" <<EOF
[Unit]
Description=GNS3 server

[Service]
Type=simple
ExecStart=/usr/bin/gns3server

[Install]
WantedBy=default.target
EOF

GNS3_PASSWORD=$(makepasswd --chars 20)
sudo su gns3 -c "mkdir -p /home/gns3/.config/GNS3/2.2"
sudo su gns3 -c "cat >/home/gns3/.config/GNS3/2.2/gns3_server.conf" <<EOF
[Server]
auth = True
user = gns3
password = $GNS3_PASSWORD
EOF

# These don't work because .bashrc doesn't get sourced to set XDG_RUNTIME_DIR
sudo su gns3 -c "systemctl --user enable gns3"
sudo su gns3 -c "systemctl --user start gns3"

echo GNS3 user = gns3
echo GNS3 password = $GNS3_PASSWORD

# Routed configuration

sudo tee /etc/systemd/system/veth.service >/dev/null <<EOF
[Unit]
Description=Configure virtual ethernet for GNS3
After=network.target
Before=isc-dhcp-server.service

[Service]
Type=oneshot
ExecStart=/sbin/ip link add dev veth type veth peer name veth-host
ExecStart=/sbin/ip link set dev veth up
ExecStart=/sbin/ip link set dev veth-host up
ExecStart=/sbin/ip addr add $FIRST_HOST/$MASKLEN broadcast $BROADCAST dev veth-host
ExecStart=/sbin/ethtool -K veth-host tx off

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable veth
sudo systemctl start veth

# You should now be able to `ping 192.168.8.1`

# sudo apt install isc-dhcp-server

sudo tee -a /etc/dhcp/dhcpd.conf >/dev/null <<EOF
      subnet $ZERO_HOST netmask 255.255.255.0 {
        range $FIRST_DHCP $LAST_DHCP;
        option routers $FIRST_HOST;
      }
EOF

# Also in `/etc/dhcp/dhcpd.conf`, set `domain-name-servers` to your local DNS servers.
# They can be found by looking at the output of `resolvectl`.
# This would ideally be done automatically
# You may also want to set `domain-name` in that same file to your local DNS name.

sudo systemctl enable isc-dhcp-server
sudo systemctl start isc-dhcp-server

# Modify `/etc/sysctl.conf` to enable packet forwarding:
sudo sed -i /net.ipv4.ip_forward=1/s/^#// /etc/sysctl.conf

# If you want to access the virtual network devices from other machines,
# you'll need to adjust your network configuration to route traffic for the virtual subnet to the machine.

# NAT
sudo iptables -t nat -A POSTROUTING -s $SUBNET -j MASQUERADE

# If you configure NAT, the following commands will make that change persist over reboots:

# sudo apt install iptables-persistent
sudo DEBIAN_FRONTEND=noninteractive dpkg-reconfigure iptables-persistent
