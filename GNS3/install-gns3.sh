#!/bin/bash
#
# This script will install a new user 'gns3' that operates a
# gns3server and keeps all of the gns3 configuration and virtual
# drives in /home/gns3.  A virtual network interface called 'veth'
# will also be created, suitable for use by gns3 cloud nodes, with the
# bare metal machine running this script configured as a DHCP server
# and NAT gateway.  Specifically, the script does the following:
#
#    - enables the gns3 PPA, if needed
#    - installs 'gns3-server', if it isn't installed already,
#      along with several subsidiary packages
#    - creates a 'gns3' user, if one doesn't already exist
#       - adds new user to groups needed to run gns3server
#       - enables user systemd services for new user
#       - installs a user systemd service to run gns3server
#       - picks a random password and sets up gns3 authentication
#    - installs a system service, it it doesn't already exist
#      to bring up a virtual link, usable by gns3 devices,
#      and assigns a static IP address range to it
#    - creates a 'dnsmasq' configuration file to provide
#      DHCP and DNS service on the virtual link
#    - installs the 'dnsmasq' package if it isn't installed
#    - turns on IPv4 packet forwarding, if necessary
#    - creates a bird configuration file, if one doesn't exist,
#      that listen on the virtual interface for OSPF
#      routing annoucements
#    - installs the 'bird' package, if it isn't installed
#
# The default subnet is 192.168.8.0/24, but this can be overridden
# by setting the SUBNET environment variable.
#
# The script will prompt interactively several times:
#    - to confirm adding the repository
#    - to confirm installing the packages
#    - to ask if non-superusers should be able to run gns3 (say yes)
#
# Environment variables can be used to silence these prompts:
#
# APT_OPTS=-y will stop prompts #1 and #2
# DEBIAN_FRONTEND=noninteractive will stop prompt #3

# This is where I'd really like to use Python
# Can't currently handle anything but a /24, due to 255.255.255.0 later in the script
# maybe shell tools prips or ipcalc

DOMAIN=test
SUBNET="${SUBNET:-192.168.8.0/24}"

MASKLEN=$(echo $SUBNET | cut -d / -f 2)

SUBNET_PREFIX=$(echo $SUBNET | cut -d . -f 1-3)

ZERO_HOST=$SUBNET_PREFIX.0
FIRST_HOST=$SUBNET_PREFIX.1
BROADCAST=$SUBNET_PREFIX.255
FIRST_DHCP=$SUBNET_PREFIX.129
LAST_DHCP=$SUBNET_PREFIX.199

if [ $EUID != 0 ]; then
    echo "You must run this command as root."
    exit 1
fi

# https://stackoverflow.com/questions/32145643/how-to-use-ctrlc-to-stop-whole-script-not-just-current-command
trap "echo; exit" INT

if [ "$1" = "remove-service" ]; then
    echo "Removing veth.service"
    systemctl disable veth
    systemctl stop veth
    rm /etc/systemd/system/veth.service
    exit 0
fi

if [ "$1" = "remove-dnsmasq" ]; then
    # to remove dnsmasq, "apt purge dnsmasq" does not remove /etc/dnsmasq.d;
    # other system packages, like 'ubuntu-fan', put files in /etc/dnsmasq.d
    echo "Removing /etc/dnsmasq.d/gns3"
    rm /etc/dnsmasq.d/gns3
    echo "Run 'apt remove dnsmasq' to remove the package"
    exit 0
fi

if [ "$1" = "purge-bird" ]; then
    # to remove dnsmasq, "apt purge dnsmasq" does not remove /etc/dnsmasq.d;
    # other system packages, like 'ubuntu-fan', put files in /etc/dnsmasq.d
    echo "Removing /etc/dnsmasq.d/gns3"
    rm /etc/dnsmasq.d/gns3
    echo "Run 'apt remove dnsmasq' to remove the package"
    exit 0
fi

# If you want to access the GNS3 devices from other machines, you'll
# need to adjust your network configuration to route traffic for the
# virtual subnet to the machine.
#
# Otherwise, call the script with 'enable-nat', and it will configure
# NAT so that the GNS3 devices will have Internet access, but only the
# local machine will have access to the GNS3 subnet.

if [ "$1" = "enable-nat" ]; then
    if iptables -t nat -L POSTROUTING -n | grep MASQUERADE | grep $SUBNET > /dev/null; then
	echo "NAT already configured for $SUBNET"
    else
	echo "Enabling NAT"
	iptables -t nat -A POSTROUTING -s $SUBNET -j MASQUERADE
	# This makes the NAT setting persist over reboots, but it only
	# works if iptables-persistent is installed.
	#
	# iptables-persistent seems a significant enough change to the
	# system that I don't do it automatically.
	if dpkg -s iptables-persistent >/dev/null 2>&1; then
	    dpkg-reconfigure iptables-persistent
	fi
    fi
fi

if [ "$1" = "disable-nat" ]; then
    if iptables -t nat -L POSTROUTING -n | grep MASQUERADE | grep $SUBNET > /dev/null; then
	echo "Disabling NAT"
	iptables -t nat -D POSTROUTING -s $SUBNET -j MASQUERADE
	# This makes the NAT setting persist over reboots, but it only
	# works if iptables-persistent is installed.
	#
	# iptables-persistent seems a significant enough change to the
	# system that I don't do it automatically.
	if dpkg -s iptables-persistent >/dev/null 2>&1; then
	    dpkg-reconfigure iptables-persistent
	fi
    else
	echo "NAT not configured for $SUBNET"
    fi
fi

if ! dpkg -s gns3-server >/dev/null 2>&1; then
    if find /etc/apt/ -name *.list | xargs cat | grep -v '^#' | grep gns3/ppa >/dev/null; then
	echo "PPA 'gns3' already added"
    else
	add-apt-repository $APT_OPTS ppa:gns3
    fi
    # Don't install recommends, because that depends on xvnc and a big
    # chuck of X11 stuff, that we don't need for a server, but we will
    # need the recommended package dynamips for the default Ethernet
    # switch.
    apt $APT_OPTS install --no-install-recommends gns3-server dynamips makepasswd genisoimage
else
    echo "package 'gns3-server' already installed"
fi

# I wondered if this should this be a --system user, but sometimes I
# want to su to gns3 to stop and restart the gns3server.

if ! id -u gns3 >/dev/null 2>&1; then
    echo "Adding gns3 user"
    adduser --gecos "GNS3 server" --disabled-password gns3

    adduser gns3 kvm
    adduser gns3 ubridge

    loginctl enable-linger gns3

    # This is for convenience when su'ing to gns3.  It makes systemctl --user work.

    echo 'export XDG_RUNTIME_DIR=/run/user/$(id -u)' | su gns3 -c "cat >>/home/gns3/.bashrc"

    su gns3 -c "mkdir -p /home/gns3/.config/systemd/user"
    su gns3 -c "cat >/home/gns3/.config/systemd/user/gns3.service" <<EOF
[Unit]
Description=GNS3 server

[Service]
Type=simple
ExecStart=/usr/bin/gns3server

[Install]
WantedBy=default.target
EOF

    GNS3_PASSWORD=$(makepasswd --chars 20)
    su gns3 -c "mkdir -p /home/gns3/.config/GNS3/2.2"
    su gns3 -c "cat >/home/gns3/.config/GNS3/2.2/gns3_server.conf" <<EOF
[Server]
auth = True
user = gns3
password = $GNS3_PASSWORD
EOF

    # .bashrc doesn't get sourced to set XDG_RUNTIME_DIR
    #
    # "In general, $HOME/.bashrc is executed for non-interactive login shells
    # but no script can be guaranteed to run for a non-interactive non-login shell."
    # https://stackoverflow.com/a/55893600/1493790
    #
    # Requesting a login shell doesn't work, either, because .bashrc starts with
    # a check for non-interactive invocation and does nothing if so.

    su gns3 -c "env XDG_RUNTIME_DIR=/run/user/$(id -u gns3) systemctl --user enable gns3"
    su gns3 -c "env XDG_RUNTIME_DIR=/run/user/$(id -u gns3) systemctl --user start gns3"
else
    echo "user 'gns3' already exists"
    GNS3_PASSWORD=$(grep password /home/gns3/.config/GNS3/2.2/gns3_server.conf | cut -d = -f 2)
fi

if ! systemctl --all --type service | grep -q veth.service; then

    echo "Installing veth.service"
    # Routed configuration

    tee /etc/systemd/system/veth.service >/dev/null <<EOF
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
ExecStart=resolvectl dns veth-host $FIRST_HOST
ExecStart=resolvectl domain veth-host $DOMAIN

[Install]
WantedBy=multi-user.target
EOF

    systemctl enable veth
    systemctl start veth
else
    echo "service 'veth' already exists"
fi

if [ ! -r /etc/dnsmasq.d/gns3 ]; then
    mkdir -p /etc/dnsmasq.d
    tee -a /etc/dnsmasq.d/gns3 >/dev/null <<EOF
listen-address=$FIRST_HOST
# Only bind to $FIRST_HOST for DNS service
bind-interfaces
dhcp-range=$FIRST_DHCP,$LAST_DHCP,12h
# Register new DHCP hosts into DNS under DOMAIN
domain=$DOMAIN
# Make DNS server authoritative for domain to avoid timeouts
# See https://unix.stackexchange.com/questions/720570
auth-zone=$DOMAIN
auth-zone=in-addr.arpa
# auth-server is required when auth-zone is defined; use a non-existent dummy server
auth-server=dns.$DOMAIN
EOF

else
    echo "'/etc/dnsmasq.d/gns3' already exists"
fi

if ! dpkg -s dnsmasq >/dev/null 2>&1; then
    apt $APT_OPTS install dnsmasq
    systemctl enable dnsmasq
    systemctl start dnsmasq
    # Now I'd like to do this, but it isn't working the way I expect
    # sudo resolvectl dns veth-host 192.168.8.1
else
    echo "package 'dnsmasq' already installed"
fi

# We need packet forwarding turned on, otherwise the virtual machines
# won't be able to access the Internet.

if grep -q '#net.ipv4.ip_forward=1' /etc/sysctl.conf; then
    echo "Modifing '/etc/sysctl.conf' to enable packet forwarding"
    sed -i /net.ipv4.ip_forward=1/s/^#// /etc/sysctl.conf
    echo "Enabling packet forwarding for current boot"
    sysctl net.ipv4.ip_forward=1
else
    echo "IPv4 packet forwarding already enabled"
fi

# It's useful to configure a minimial OSPF routing environment, to
# allow devices in the virtual network to advertise routes to the bare
# metal machine.

if [ ! -r /etc/bird/bird.conf ]; then
    mkdir -p /etc/bird
    tee -a /etc/bird/bird.conf >/dev/null <<EOF
# This is a minimalist bird configuration that listens for OSPF annoucements
# on the virtual link used by GNS3 virtual networks.
#
# The Device protocol is not a real routing protocol. It doesn't generate any
# routes and it only serves as a module for getting information about network
# interfaces from the kernel.

protocol device {
}

# The Kernel protocol is not a real routing protocol. Instead of communicating
# with other routers in the network, it performs synchronization of BIRD's
# routing tables with the OS kernel.
protocol kernel {
	metric 64;	# Use explicit kernel route metric to avoid collisions
			# with non-BIRD routes in the kernel routing table
	import none;
	export all;	# Actually insert routes into the kernel routing table
}

protocol ospf OSPF {
	area 0.0.0.0 {
		interface "veth-host" {
			  cost 10;
		};
	};
	import all;
	export none;
}
EOF
else
    echo "'/etc/bird/bird.conf' already exists"
fi

if ! dpkg -s bird >/dev/null 2>&1; then
    # noniteractive, because we've set a non-standard configuration file,
    # and otherwise it will prompt us if we want to keep the "old" version
    DEBIAN_FRONTEND=noninteractive apt $APT_OPTS install bird
else
    echo "package 'bird' already installed"
fi

echo
echo GNS3 user = gns3
echo GNS3 password = $GNS3_PASSWORD
