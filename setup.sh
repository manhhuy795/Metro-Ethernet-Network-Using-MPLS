#!/bin/bash
set -euo pipefail

GRN='\033[0;32m'; YLW='\033[1;33m'; RED='\033[0;31m'; CYN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
info() { echo -e "${CYN}[*]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }

if [[ $EUID -ne 0 ]]; then
  err "Run with sudo: sudo bash setup.sh"
  exit 1
fi

info "Updating package index..."
apt-get update -y

info "Installing Mininet, OVS, FRRouting and test tools..."
apt-get install -y \
  mininet openvswitch-switch openvswitch-common \
  frr frr-pythontools \
  iperf3 tcpdump xterm iproute2 iputils-ping \
  python3 python3-pip python3-matplotlib python3-numpy \
  net-tools curl ethtool bridge-utils util-linux

systemctl enable openvswitch-switch >/dev/null 2>&1 || true
systemctl start openvswitch-switch 2>/dev/null || true

info "Preparing FRR user/group permissions..."
if getent group frrvty >/dev/null 2>&1; then
  if id frr >/dev/null 2>&1; then usermod -aG frrvty frr >/dev/null 2>&1 || true; fi
  usermod -aG frrvty root >/dev/null 2>&1 || true
fi
mkdir -p /var/run/frr /tmp/frr-mpls-lab
if id frr >/dev/null 2>&1 && getent group frrvty >/dev/null 2>&1; then
  chown -R frr:frrvty /var/run/frr /tmp/frr-mpls-lab 2>/dev/null || true
  chmod 775 /var/run/frr /tmp/frr-mpls-lab 2>/dev/null || true
else
  chmod 755 /var/run/frr 2>/dev/null || true
  chmod 777 /tmp/frr-mpls-lab 2>/dev/null || true
fi
ok "FRR permissions prepared"

# The lab starts one FRR instance per Mininet namespace. Stop host FRR so the
# default /var/run/frr/*.vty sockets do not conflict with per-node sockets.
systemctl stop frr >/dev/null 2>&1 || true
systemctl disable frr >/dev/null 2>&1 || true

info "Loading kernel modules..."
for mod in 8021q mpls_router mpls_iptunnel mpls_gso ip_tunnel ip_gre; do
  if modprobe "$mod" 2>/dev/null; then
    ok "modprobe $mod"
  else
    warn "$mod: not available or already built in"
  fi
done

info "Applying sysctl settings for routing/MPLS..."
cat >/etc/sysctl.d/99-mpls-lab.conf <<'EOF2'
net.mpls.platform_labels = 1048575
net.mpls.conf.default.input = 1
net.ipv4.ip_forward = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOF2
sysctl --system >/dev/null 2>&1 || true

info "Installing safe v/vtysh dispatchers for Mininet nodes..."
REAL_VTYSH=""
if [[ -x /usr/bin/vtysh ]]; then
  REAL_VTYSH="/usr/bin/vtysh"
elif [[ -x /usr/lib/frr/vtysh ]]; then
  REAL_VTYSH="/usr/lib/frr/vtysh"
fi

# Both dispatchers forward to the node-local wrapper created by topology.py:
#   /tmp/frr-mpls-lab/<node>/v
# Recommended CLI syntax:
#   P1 v show mpls ldp neighbor
#   spine1 v show ip ospf neighbor
# Compatibility syntax:
#   P1 vtysh -c "show mpls ldp neighbor"
cat >/usr/local/bin/v <<'EOFV'
#!/usr/bin/env bash
set +e
BASE=/tmp/frr-mpls-lab
links="$(ip -o link show 2>/dev/null || true)"
node=""
case "$links" in
  *p1-pe1*|*p1-p2*|*p1-p3*|*p1-p4*) node="P1" ;;
  *p2-p1*|*p2-p3*|*p2-p4*|*p2-pe3*) node="P2" ;;
  *p3-pe1*|*p3-p1*|*p3-p2*|*p3-p4*|*p3-pe2*) node="P3" ;;
  *p4-p1*|*p4-p2*|*p4-p3*|*p4-pe2*|*p4-pe3*) node="P4" ;;
  *pe1-p1*|*pe1-p3*|*pe1-wan*) node="PE1" ;;
  *pe2-p3*|*pe2-p4*|*pe2-wan*) node="PE2" ;;
  *pe3-p2*|*pe3-p4*|*pe3-wan*) node="PE3" ;;
  *ce1-wan*|*ce1-lan*) node="CE1" ;;
  *ce2-wan*|*ce2-lan*) node="CE2" ;;
  *ce3-wan*|*ce3-spine1*|*ce3-spine2*) node="CE3" ;;
  *spine1-ce3*|*spine1-leaf1*|*spine1-leaf2*|*spine1-leaf3*) node="spine1" ;;
  *spine2-ce3*|*spine2-leaf1*|*spine2-leaf2*|*spine2-leaf3*) node="spine2" ;;
  *leaf1-spine1*|*leaf1-spine2*) node="leaf1" ;;
  *leaf2-spine1*|*leaf2-spine2*) node="leaf2" ;;
  *leaf3-spine1*|*leaf3-spine2*) node="leaf3" ;;
esac
if [[ -n "$node" && -x "$BASE/$node/v" ]]; then
  exec "$BASE/$node/v" "$@"
fi
echo "Cannot identify Mininet node for v dispatcher. Use: <NODE> /tmp/frr-mpls-lab/<NODE>/v <show command>" >&2
exit 127
EOFV
chmod 755 /usr/local/bin/v

cat >/usr/local/bin/vtysh <<'EOFVTY'
#!/usr/bin/env bash
set +e
BASE=/tmp/frr-mpls-lab
links="$(ip -o link show 2>/dev/null || true)"
node=""
case "$links" in
  *p1-pe1*|*p1-p2*|*p1-p3*|*p1-p4*) node="P1" ;;
  *p2-p1*|*p2-p3*|*p2-p4*|*p2-pe3*) node="P2" ;;
  *p3-pe1*|*p3-p1*|*p3-p2*|*p3-p4*|*p3-pe2*) node="P3" ;;
  *p4-p1*|*p4-p2*|*p4-p3*|*p4-pe2*|*p4-pe3*) node="P4" ;;
  *pe1-p1*|*pe1-p3*|*pe1-wan*) node="PE1" ;;
  *pe2-p3*|*pe2-p4*|*pe2-wan*) node="PE2" ;;
  *pe3-p2*|*pe3-p4*|*pe3-wan*) node="PE3" ;;
  *ce1-wan*|*ce1-lan*) node="CE1" ;;
  *ce2-wan*|*ce2-lan*) node="CE2" ;;
  *ce3-wan*|*ce3-spine1*|*ce3-spine2*) node="CE3" ;;
  *spine1-ce3*|*spine1-leaf1*|*spine1-leaf2*|*spine1-leaf3*) node="spine1" ;;
  *spine2-ce3*|*spine2-leaf1*|*spine2-leaf2*|*spine2-leaf3*) node="spine2" ;;
  *leaf1-spine1*|*leaf1-spine2*) node="leaf1" ;;
  *leaf2-spine1*|*leaf2-spine2*) node="leaf2" ;;
  *leaf3-spine1*|*leaf3-spine2*) node="leaf3" ;;
esac
if [[ -n "$node" && -x "$BASE/$node/v" ]]; then
  exec "$BASE/$node/v" "$@"
fi
echo "Cannot identify Mininet node for vtysh dispatcher. Use: <NODE> v <show command>" >&2
echo "Example: P1 v show mpls ldp neighbor" >&2
exit 127
EOFVTY
chmod 755 /usr/local/bin/vtysh
ok "Installed /usr/local/bin/v and /usr/local/bin/vtysh dispatchers"

mkdir -p /tmp/mpls_results /tmp/frr-mpls-lab /var/run/frr
ln -sfn /tmp/frr-mpls-lab /tmp/mpls_frr_lab 2>/dev/null || true
chmod 777 /tmp/mpls_results /tmp/frr-mpls-lab || true
chmod 755 /var/run/frr || true

ok "Environment is ready."
echo
echo "Clean old Mininet state if needed:"
echo "  sudo mn -c"
echo
echo "Run MPLS topology:"
echo "  sudo python3 topology.py"
echo "  # inside Mininet CLI:"
echo "Verification examples inside Mininet CLI:"
echo "  verify"
echo "  vsh P1 show mpls ldp binding"
echo "  vsh P1 show mpls ldp neighbor"
echo "  P1 v show mpls ldp binding"
echo "  P1 v show mpls ldp neighbor"
echo "  P1 ip -f mpls route"
echo "  spine1 v show ip ospf neighbor"
echo "  spine1 v show ip ospf route"
echo "  CE3 v show ip route"
echo "  spine1 ip route"
echo "  PE1 ip link show type gretap"
echo "  PE1 bridge fdb show dev br-vpls"
echo "  core1 ovs-vsctl show"
echo "  CE2 sh -c \"ip -brief addr show | grep ce2-lan\""
echo "  CE2 ip route"
echo "  CE2 v show ip route"
echo "  H2_1 ping -c 2 192.168.21.11"
echo
echo "Create report after tests:"
echo "  python3 analyze.py mpls_results/results_mpls_*.json --html"
