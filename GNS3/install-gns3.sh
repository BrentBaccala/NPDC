#!/bin/bash
#
# This script will install a new user 'gns3' that operates a
# gns3server and keeps all of the gns3 configuration and virtual
# drives in /home/gns3.  A virtual network interface called 'veth'
# will also be created, suitable for use by gns3 cloud nodes, with the
# bare metal machine running this script configured as a DHCP server
# and NAT gateway.  The gns3 PPA is added as an apt repository, and
# necessary packages are installed.
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

[Install]
WantedBy=multi-user.target
EOF

    systemctl enable veth
    systemctl start veth
else
    echo "service 'veth' already exists"
fi

if ! dpkg -s dnsmasq >/dev/null 2>&1; then

    mkdir -p /etc/dnsmasq.d
    tee -a /etc/dnsmasq.d/gns3 >/dev/null <<EOF
listen-address=$FIRST_HOST
# Only bind to $FIRST_HOST for DNS service
bind-interfaces
dhcp-range=$FIRST_DHCP,$LAST_DHCP,12h
EOF

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

# If you want to access the GNS3 devices from other machines, you'll
# need to adjust your network configuration to route traffic for the
# virtual subnet to the machine.
#
# Otherwise, only the local machine will have access to the GNS3
# subnet, so configure NAT to give Internet access to the GNS3
# devices.

CONFIGURE_NAT=false

if $CONFIGURE_NAT; then
    if iptables -t nat -L POSTROUTING -n | grep MASQUERADE | grep $SUBNET > /dev/null; then
	echo "NAT already configured for $SUBNET"
    else
	iptables -t nat -A POSTROUTING -s $SUBNET -j MASQUERADE
	# This makes the NAT setting persist over reboots, but it only
	# works if iptables-persistent is installed.
	if dpkg -s iptables-persistent >/dev/null 2>&1; then
	    dpkg-reconfigure iptables-persistent
	fi
    fi
fi

echo
echo GNS3 user = gns3
echo GNS3 password = $GNS3_PASSWORD
