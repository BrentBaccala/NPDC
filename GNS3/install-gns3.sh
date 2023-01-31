#!/bin/bash
#
# This script will install a new user 'gns3' that operates a
# gns3server and keeps all of the gns3 configuration and virtual
# drives in /home/gns3.  A virtual network interface called 'veth'
# will also be created, suitable for use by gns3 cloud nodes, with the
# bare metal machine running this script configured as a DHCP server,
# DNS server, OSPF speaker, and NAT gateway.  Specifically, the script
# does the following:
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
#      and assigns a static private IP address range to it
#    - installs the 'bind9' package if it isn't installed
#    - creates the Bind configuration files to provide
#      DNS service on the virtual link
#    - installs the 'isc-dhcp-server' package if it isn't installed
#    - creates the dhcpd.conf configuration file to provide
#      DHCP service on the virtual link
#    - turns on IPv4 packet forwarding, if necessary
#    - creates a bird configuration file, if one doesn't exist,
#      that listen on the virtual interface for OSPF
#      routing annoucements
#    - installs the 'bird' package, if it isn't installed
#
# There are two important environment variables you can set:
#
# SUBNET           virtual link's subnet (default is a random /24 in 10.0.0.0/8)
# DOMAIN           virtual link's DNS domain (default is the bare metal machine's hostname)
#
# There's also some options you can give as the first argument:
#
# remove-service: removes the virtual link (veth) service
# enable-nat:     adds NAT rule
# disable-nat:    removes NAT rule
#                 both NAT commands save the iptables rules over
#                 reboots, if iptables-persistent is installed
#
# To completely undo what this script does, do the following (as root):
#    - su gns3 -c "env XDG_RUNTIME_DIR=/run/user/$(id -u gns3) systemctl --user stop gns3"
#    - deluser --remove-home gns3
#    - ./install-gns3.sh remove-service
#    - apt purge gns3-server dynamips makepasswd genisoimage bind9 isc-dhcp-server bird
#    - ./install-gns3.sh disable-nat
#    - add-apt-repository --remove ppa:gns3

HOSTNAME=$(hostname)

if [ $EUID != 0 ]; then
    echo "You must run this command as root."
    exit 1
fi

# https://stackoverflow.com/questions/32145643/how-to-use-ctrlc-to-stop-whole-script-not-just-current-command
trap "echo; exit" INT

if systemctl --all --type service | grep -q veth.service; then

    # The script has already run at least once.  Make sure all the settings are consistent.

    VETH_DOMAIN=$(grep 'resolvectl domain' /etc/systemd/system/veth.service | sed 's/.* //')
    if [ "$VETH_DOMAIN" == "" ]; then
	VETH_DOMAIN=$(grep 'set-domain' /etc/systemd/system/veth.service | sed 's/.*=//')
    fi
    VETH_SUBNET=$(grep 'ip addr add' /etc/systemd/system/veth.service | sed -E 's|.* ([.0-9]*/[0-9]*).*|\1|')

    DOMAIN="${DOMAIN:-$VETH_DOMAIN}"
    SUBNET="${SUBNET:-$VETH_SUBNET}"

    if [ "$VETH_DOMAIN" != "$DOMAIN" ]; then
	echo "DNS domain in veth.service ($VETH_DOMAIN) does not match script's DOMAIN variable ($DOMAIN)"
	exit 1
    fi
    if [ "$VETH_SUBNET" != "$SUBNET" ]; then
	echo "Subnet in veth.service ($VETH_SUBNET) does not match script's SUBNET variable ($SUBNET)"
	exit 1
    fi
else
    # The script has not run before.  Use DOMAIN and SUBNET from environment variables, or take defaults
    DOMAIN="${DOMAIN:-$(hostname)}"
    SUBNET="${SUBNET:-10.$(($RANDOM%256)).$(($RANDOM%256)).0/24}"
fi

function need_ppa() {
    if find /etc/apt/ -name *.list | xargs cat | grep -v '^#' | grep $1/ppa >/dev/null; then
	echo "PPA '$1' already added"
    else
	add-apt-repository -y ppa:$1
    fi
}

function need_pkg() {
    if ! dpkg -s $1 >/dev/null 2>&1; then
	# --no-install-recommends, because gns3-server depends on
	# xvnc and a big chuck of X11 stuff, that we don't need for a
	# server.
	#
	# noniteractive, because we've set a non-standard configuration file,
	# and otherwise it will prompt us if we want to keep the "old" version
	#
	# setting noniteractive also silences a question about whether non-root
	# users should be allowed to run gns3, and takes the default "yes"
	#
	# --force-confold because this script sets its custom configurations
	# by checking to see if the configuration files are present, leaving
	# them alone if they're not, otherwise creating them, then installing
	# the package.  So we want to keep our "old" configuration files, i.e,
	# the ones we just created, instead of the ones in the package.
	DEBIAN_FRONTEND=noninteractive apt -y -o Dpkg::Options::="--force-confold" install --no-install-recommends $1
    else
	echo "package '$1' already installed"
    fi
}

# This is where I'd really like to use Python
# Can't currently handle anything but a /24, due to 255.255.255.0 later in the script
# maybe shell tools prips or ipcalc

MASKLEN=$(echo $SUBNET | cut -d / -f 2)

SUBNET_PREFIX=$(echo $SUBNET | cut -d . -f 1-3)

ZERO_HOST=$SUBNET_PREFIX.0
FIRST_HOST=$SUBNET_PREFIX.1
BROADCAST=$SUBNET_PREFIX.255
FIRST_DHCP=$SUBNET_PREFIX.129
LAST_DHCP=$SUBNET_PREFIX.199

if [ "$1" = "remove-service" ]; then
    echo "Removing veth.service"
    systemctl disable veth
    systemctl stop veth
    rm /etc/systemd/system/veth.service
    systemctl daemon-reload
    systemctl reset-failed
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
    exit 0
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
    exit 0
fi

need_ppa gns3

need_pkg gns3-server
need_pkg dynamips
need_pkg makepasswd
need_pkg genisoimage

# I wondered if this should this be a --system user, but sometimes I
# want to su to gns3 to stop and restart the gns3server, and system
# users don't have shells.

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

    if which resolvectl >& /dev/null; then
	# Ubuntu 20.04 LTS
	ADD_DNS_COMMAND="ExecStart=/usr/bin/resolvectl dns veth-host $FIRST_HOST"
	ADD_DOMAIN_COMMAND="ExecStart=/usr/bin/resolvectl domain veth-host $DOMAIN"
    elif which systemd-resolve >& /dev/null; then
	# Ubuntu 18.04 LTS
	ADD_DNS_COMMAND="ExecStart=/usr/bin/systemd-resolve --interface=veth-host --set-dns=$FIRST_HOST"
	ADD_DOMAIN_COMMAND="ExecStart=/usr/bin/systemd-resolve --interface=veth-host --set-domain=$DOMAIN"
    else
	ADD_DNS_COMMAND=""
	ADD_DOMAIN_COMMAND=""
	echo "Neither resolvectl nor systemd-resolve available; veth service won't configure DNS"
    fi
    tee /etc/systemd/system/veth.service >/dev/null <<EOF
[Unit]
Description=Configure virtual ethernet for GNS3
After=network.target
# isc-dhcp-server won't bind to interfaces that don't exist when it starts
Before=isc-dhcp-server.service
# If bird has no interfaces when it starts, and no router id configured, it will exit with failure
Before=bird.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip link add dev veth type veth peer name veth-host
ExecStart=/sbin/ip link set dev veth up
ExecStart=/sbin/ip link set dev veth-host up
ExecStart=/sbin/ip addr add $FIRST_HOST/$MASKLEN broadcast $BROADCAST dev veth-host
ExecStart=/sbin/ethtool -K veth-host tx off
$ADD_DNS_COMMAND
$ADD_DOMAIN_COMMAND
ExecStop=/sbin/ip link del dev veth

[Install]
WantedBy=multi-user.target
EOF

    systemctl enable veth
    systemctl start veth
else
    echo "service 'veth' already exists"
fi

# DNS server
#
# It's configured to resolve everything except $DOMAIN by passing the
# query on to resolved listening on 127.0.0.53, but resolved returned
# NOTIMP errors until I turned off EDNS, and then I got complaints
# from bind about "broken trust chain", so I turned off dnssec
# validation as well.
#
# I want to use resolved because it's Ubuntu's standard DNS resolver.
#
# The $DOMAIN is configured to accept dynamic DNS updates from
# $SUBNET, and the DHCP server below will use that feature to inject
# dynamic hostnames into DNS.  If that's all we wanted, dnsmasq would
# work fine.  But we also listen for OSPF speakers in the virtual
# network and let them inject routes into our routing table (see the
# bird configuration below), so we also want to let those devices
# inject DNS names as well, and dnsmasq can't do that.
#
# There's no strong authentication configured; anything on the virtual
# network can update $DOMAIN.

if [ ! -r /etc/bind/named.conf.options ]; then
    mkdir -p /etc/bind
    tee -a /etc/bind/named.conf.options >/dev/null <<EOF
options {
	directory "/var/cache/bind";

	forward only;
	forwarders { 127.0.0.53; };

	dnssec-enable no;
	dnssec-validation no;

	listen-on { $FIRST_HOST; };
	listen-on-v6 { none; };
};

server 127.0.0.53 {
	edns no;
};
EOF
else
    echo "/etc/bind/named.conf.options already exists"
fi

REVERSE_DOMAIN=$(echo $ZERO_HOST | tr . \\n | tac | tr \\n . | sed 's/^0\.//')in-addr.arpa

if [ ! -r /etc/bind/named.conf.local ]; then
    mkdir -p /etc/bind
    tee -a /etc/bind/named.conf.local >/dev/null <<EOF
include "/etc/bind/rndc.key";

zone "$DOMAIN" {
	type master;
	file "/var/lib/bind/$DOMAIN.zone";
	allow-update { $SUBNET; key rndc-key; };
};

zone "$REVERSE_DOMAIN" {
	type master;
	file "/var/lib/bind/$ZERO_HOST.zone";
	allow-update { $SUBNET; key rndc-key; };
};
EOF
else
    echo "/etc/bind/named.conf.local already exists"
fi

# Set SOA minimum (TTL for negative caching) to 60 seconds
#
# Since DHCP lease times are 120 seconds, their dynamic DNS records
# are 60 seconds (1/2 lease time; documented in source code)
#
# Negative caching for the same period seems reasonable

if [ ! -r /var/lib/bind/$DOMAIN.zone ]; then
    mkdir -p /var/lib/bind
    tee -a /var/lib/bind/$DOMAIN.zone >/dev/null <<EOF
\$ORIGIN $DOMAIN.
\$TTL 604800 ; 1 week
@ IN SOA $HOSTNAME.$DOMAIN. dnsadmin.$DOMAIN. (
   2022102301 ; serial
   28800   ; refresh (8 hours)
   3600    ; retry (1 hour)
   302400  ; expire (3 days 12 hours)
   60      ; minimum (1 minute)
   )
   NS $HOSTNAME.$DOMAIN.
$HOSTNAME  A $FIRST_HOST
EOF
else
    echo "/var/lib/bind/$DOMAIN.zone already exists"
fi

if [ ! -r /var/lib/bind/$ZERO_HOST.zone ]; then
    mkdir -p /var/lib/bind
    tee -a /var/lib/bind/$ZERO_HOST.zone >/dev/null <<EOF
\$ORIGIN $REVERSE_DOMAIN.
\$TTL 604800 ; 1 week
@ IN SOA $HOSTNAME.$DOMAIN. dnsadmin.$DOMAIN. (
   2022102301 ; serial
   28800   ; refresh (8 hours)
   3600    ; retry (1 hour)
   302400  ; expire (3 days 12 hours)
   60      ; minimum (1 minute)
   )
   NS $HOSTNAME.$DOMAIN.
EOF
else
    echo "/var/lib/bind/$ZERO_HOST.zone already exists"
fi

# DHCP server: 120 second lease time because I'm tearing down and
# rebulding the virtual network so often.
#
# I tried doing this with a 10 second lease time, but that causes
# OSPF route flaps (either bird or frr) on the virtual nodes.

if [ ! -r /etc/dhcp/dhcpd.conf ]; then
    mkdir -p /etc/dhcp
    tee -a /etc/dhcp/dhcpd.conf >/dev/null <<EOF
ddns-updates on;
ddns-update-style standard;
update-optimization off;
authoritative;

include "/etc/dhcp/rndc.key";

allow unknown-clients;
default-lease-time 120;
max-lease-time 120;
log-facility local7;

zone $DOMAIN. { key rndc-key; }
zone $REVERSE_DOMAIN. { primary $HOSTNAME.$DOMAIN; key rndc-key; }

subnet $ZERO_HOST netmask 255.255.255.0 {
 range $FIRST_DHCP $LAST_DHCP;
 option subnet-mask 255.255.255.0;
 option domain-name-servers $FIRST_HOST;
 option domain-name "$DOMAIN";
 option routers $FIRST_HOST;
 option broadcast-address $BROADCAST;
}
EOF
else
    echo "/etc/dhcp/dhcpd.conf already exists"
fi

need_pkg bind9
need_pkg isc-dhcp-server

# We had to wait bind to be installed for this to work, since
# otherwise we might not have a 'bind' user, and yes, the
# files need to be writable by bind to allow dynamic updates.

chown bind.bind /var/lib/bind/$DOMAIN.zone
chown bind.bind /var/lib/bind/$ZERO_HOST.zone

# When the bind package installed, it created an access key.
# dhcpd needs it to update DNS entries.  Copy the file
# to a location and permission accessible to dhcpd.
#
# dhcpd can't read the key if its permissions are 440.

cp /etc/bind/rndc.key /etc/dhcp/
chmod 444 /etc/dhcp/rndc.key
chown dhcpd.dhcpd /etc/dhcp/rndc.key

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

need_pkg bird

echo
echo GNS3 user = gns3
echo GNS3 password = $GNS3_PASSWORD
