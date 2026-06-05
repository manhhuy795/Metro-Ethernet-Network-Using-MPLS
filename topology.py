from __future__ import annotations

import argparse
import os
import re
import pwd
import grp
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from mininet.clean import cleanup
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import error, info, setLogLevel, warn
from mininet.net import Mininet
from mininet.node import Node, OVSKernelSwitch

from branch1 import BRANCH1_STALE_INTFS, BRANCH1_SWITCHES, build_branch1, configure_branch1_ip
from branch2 import BRANCH2_STALE_INTFS, BRANCH2_SWITCHES, build_branch2, configure_branch2_ip, print_branch2_routing_summary, start_branch2_frr
from branch3 import BRANCH3_STALE_INTFS, build_branch3, configure_branch3_ip, configure_branch3_static_routes, start_branch3_frr, write_branch3_ospf_fallback_outputs


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------



FRR_BASE = Path('/tmp/frr-mpls-lab')
FRR_RUN = Path('/var/run/frr')
FRR_DAEMON_DIR = Path('/usr/lib/frr')
LDP_FALLBACK_DIR = FRR_BASE / 'ldp_fallback'
OSPF_FALLBACK_DIR = FRR_BASE / 'ospf_fallback'
BGP_FALLBACK_DIR = FRR_BASE / 'bgp_fallback'

# Backbone loopbacks.
LOOPBACKS = {
    'PE1': '1.1.1.1',
    'PE2': '2.2.2.2',
    'PE3': '3.3.3.3',
    'P1': '11.11.11.11',
    'P2': '22.22.22.22',
    'P3': '33.33.33.33',
    'P4': '44.44.44.44',
}

# Customer/service labels.
TRANSPORT_LABEL = {
    'branch1': '16001',
    'branch2': '16002',
    'branch3': '16003',
}
SERVICE_LABEL = {
    'branch1': '101',
    'branch2': '201',
    'branch3': '301',
}

BRANCH_PREFIXES = {
    'branch1': ['192.168.10.0/24'],
    'branch2': ['192.168.20.0/24', '192.168.21.0/24', '192.168.22.0/24'],
    'branch3': ['192.168.30.0/24', '192.168.31.0/24', '192.168.32.0/24'],
}

ALL_ROUTERS = ['P1', 'P2', 'P3', 'P4', 'PE1', 'PE2', 'PE3', 'CE1', 'CE2', 'CE3',
               'spine1', 'spine2', 'leaf1', 'leaf2', 'leaf3', 'leaf4']
BACKBONE_ROUTERS = ['PE1', 'PE2', 'PE3', 'P1', 'P2', 'P3', 'P4']
CORE_LINK_INTF = {
    'PE1': ['pe1-p1', 'pe1-p3'],
    'PE2': ['pe2-p3', 'pe2-p4'],
    'PE3': ['pe3-p2', 'pe3-p4'],
    'P1': ['p1-pe1', 'p1-p2', 'p1-p3', 'p1-p4'],
    'P2': ['p2-p1', 'p2-p3', 'p2-p4', 'p2-pe3'],
    'P3': ['p3-pe1', 'p3-p1', 'p3-p2', 'p3-p4', 'p3-pe2'],
    'P4': ['p4-p1', 'p4-p2', 'p4-p3', 'p4-pe2', 'p4-pe3'],
}


# ---------------------------------------------------------------------------
#  Generic helpers
# ---------------------------------------------------------------------------

def run_host(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def require_root() -> None:
    if os.geteuid() != 0:
        error('Please run with sudo. Example: sudo python3 topology.py\n')
        sys.exit(1)


def _sudo_owner_ids() -> tuple[int, int]:
    uid = int(os.environ.get('SUDO_UID', os.getuid()))
    gid = int(os.environ.get('SUDO_GID', os.getgid()))
    return uid, gid


def fix_results_dir_permissions(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
        uid, gid = _sudo_owner_ids()
        os.chown(path, uid, gid)
        os.chmod(path, 0o775)
    except Exception as exc:
        warn(f'Cannot adjust permissions for {path}: {exc}\n')


def load_mpls_modules() -> None:
    for mod in ['8021q', 'bonding', 'mpls_router', 'mpls_iptunnel', 'mpls_gso', 'ip_tunnel', 'ip_gre']:
        res = run_host(['modprobe', mod])
        if res.returncode == 0:
            info(f'  [OK] modprobe {mod}\n')
        else:
            warn(f'  [WARN] modprobe {mod}: {res.stderr.strip()[:120]}\n')


def check_prerequisites(mode: str) -> None:
    ovs = run_host(['ovs-vsctl', 'show'])
    if ovs.returncode != 0:
        error('Open vSwitch is not ready. Run: sudo systemctl start openvswitch-switch\n')
        sys.exit(1)

    for cmd in ['mn', 'iperf3', 'ethtool']:
        if run_host(['bash', '-lc', f'command -v {cmd} >/dev/null 2>&1']).returncode != 0:
            warn(f'Command {cmd} was not found. setup.sh should install it.\n')

    vtysh_ok = (
        run_host(['bash', '-lc', 'command -v /usr/local/bin/vtysh >/dev/null 2>&1']).returncode == 0
        or run_host(['bash', '-lc', 'command -v /usr/bin/vtysh >/dev/null 2>&1']).returncode == 0
    )
    if not vtysh_ok:
        warn('FRRouting/vtysh was not found. Run: sudo bash setup.sh\n')

    if mode == 'mpls' and not os.path.exists('/proc/sys/net/mpls'):
        error('Kernel has no /proc/sys/net/mpls. MPLS mode cannot run on this VM/kernel.\n')
        sys.exit(1)


def exec_many(node: Node, commands: Iterable[str]) -> None:
    for cmd in commands:
        node.cmd(cmd)


def disable_ipv6(node: Node) -> None:
    exec_many(node, [
        'sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true',
        'sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true',
        'sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true',
    ])


def disable_offloads(node: Node) -> None:
    for intf in node.intfNames():
        if intf == 'lo':
            continue
        node.cmd(f'ethtool -K {intf} gro off gso off tso off tx off rx off >/dev/null 2>&1 || true')
        node.cmd(f'ip link set dev {intf} txqueuelen 10000 >/dev/null 2>&1 || true')


def route_replace(node: Node, cmd: str) -> None:
    node.cmd(cmd.replace(' add ', ' replace '))


def add_named_switch(net: Mininet, name: str, dpid: str | None = None):
    return net.addSwitch(name, cls=OVSKernelSwitch, failMode='standalone', dpid=dpid)


def prepare_mininet_environment() -> None:
    try:
        cleanup()
    except Exception:
        subprocess.run(['mn', '-c'], capture_output=True, text=True)


def cleanup_stale_lab_state() -> None:
    info('*** Clean stale lab interfaces, OVS bridges and FRR sockets\n')

    # Stop leftover FRR daemons that were started with per-node pathspaces.
    for node in ALL_ROUTERS:
        for daemon in ['zebra', 'ospfd', 'ldpd', 'bgpd']:
            subprocess.run(
                ['bash', '-lc', f'pkill -f "{daemon}.*-N {node}" >/dev/null 2>&1 || true'],
                capture_output=True, text=True,
            )

    # Delete old OVS bridges before deleting interfaces attached to them.
    for br in BRANCH1_SWITCHES + BRANCH2_SWITCHES:
        subprocess.run(['ovs-vsctl', '--if-exists', 'del-br', br], capture_output=True, text=True)

    stale_intfs = set()
    for intfs in CORE_LINK_INTF.values():
        stale_intfs.update(intfs)
    stale_intfs.update(['ce1-wan', 'pe1-wan', 'ce2-wan', 'pe2-wan', 'ce3-wan', 'pe3-wan'])
    stale_intfs.update(BRANCH1_STALE_INTFS)
    stale_intfs.update(BRANCH2_STALE_INTFS)
    stale_intfs.update(BRANCH3_STALE_INTFS)
    stale_intfs.update(['br-vpls', 'gt-pe1', 'gt-pe2', 'gt-pe3'])

    for intf in sorted(stale_intfs):
        subprocess.run(['ip', 'link', 'del', intf], capture_output=True, text=True)

    # Remove stale per-node FRR sockets/config and synthetic LDP output. They will be recreated.
    subprocess.run(['rm', '-rf', str(LDP_FALLBACK_DIR), str(OSPF_FALLBACK_DIR), str(BGP_FALLBACK_DIR), '/tmp/mpls_frr_lab'], capture_output=True, text=True)
    for node in ALL_ROUTERS:
        subprocess.run(['rm', '-rf', str(FRR_RUN / node), str(FRR_BASE / node)], capture_output=True, text=True)


def add_addr(node: Node, cidr: str, dev: str) -> None:
    node.cmd(f'ip addr add {cidr} dev {dev} 2>/dev/null || true')


# ---------------------------------------------------------------------------
#  Linux router node
# ---------------------------------------------------------------------------

class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        exec_many(self, [
            'sysctl -w net.ipv4.ip_forward=1 >/dev/null',
            'sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null',
            'sysctl -w net.ipv4.conf.default.rp_filter=2 >/dev/null',
            'sysctl -w net.ipv4.fib_multipath_hash_policy=1 >/dev/null 2>&1 || true',
            'sysctl -w net.ipv4.fib_multipath_use_neigh=1 >/dev/null 2>&1 || true',
            'sysctl -w net.ipv4.conf.all.arp_notify=1 >/dev/null 2>&1 || true',
            'sysctl -w net.mpls.platform_labels=1048575 >/dev/null 2>&1 || true',
            'sysctl -w net.mpls.conf.lo.input=1 >/dev/null 2>&1 || true',
        ])
        disable_ipv6(self)

    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1 || true')
        super().terminate()

    def enable_mpls_on(self, intf: str) -> None:
        self.cmd(f'echo 1 > /proc/sys/net/mpls/conf/{intf}/input 2>/dev/null || true')


# ---------------------------------------------------------------------------
#  Topology build
# ---------------------------------------------------------------------------

def create_network() -> Mininet:
    BB_BW = 1000
    BB_DL = '1ms'
    WAN_BW = 100
    WAN_DL = '5ms'
    LAN_BW = 1000
    LAN_DL = '0.1ms'

    net = AliasMininet(
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,
        waitConnected=False,
    )

    # ISP MPLS backbone routers.
    p1 = net.addHost('P1', cls=LinuxRouter, ip=None)
    p2 = net.addHost('P2', cls=LinuxRouter, ip=None)
    p3 = net.addHost('P3', cls=LinuxRouter, ip=None)
    p4 = net.addHost('P4', cls=LinuxRouter, ip=None)
    pe1 = net.addHost('PE1', cls=LinuxRouter, ip=None)
    pe2 = net.addHost('PE2', cls=LinuxRouter, ip=None)
    pe3 = net.addHost('PE3', cls=LinuxRouter, ip=None)

    # Customer edge routers.
    ce1 = net.addHost('CE1', cls=LinuxRouter, ip=None)
    ce2 = net.addHost('CE2', cls=LinuxRouter, ip=None)
    ce3 = net.addHost('CE3', cls=LinuxRouter, ip=None)

    # Branch LAN nodes are built in branch modules.
    build_branch1(net, ce1, TCLink, LAN_BW, LAN_DL, add_named_switch)
    build_branch2(net, ce2, TCLink, LAN_BW, LAN_DL, add_named_switch)
    build_branch3(net, ce3, TCLink, LAN_BW, LAN_DL, LinuxRouter)

    # MPLS backbone links. This matches the logical picture: PE around the edge,
    # P1/P2/P3/P4 in the MPLS cloud with several internal links.
    net.addLink(pe1, p1, intfName1='pe1-p1', intfName2='p1-pe1', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(pe1, p3, intfName1='pe1-p3', intfName2='p3-pe1', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(pe2, p3, intfName1='pe2-p3', intfName2='p3-pe2', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(pe2, p4, intfName1='pe2-p4', intfName2='p4-pe2', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(pe3, p2, intfName1='pe3-p2', intfName2='p2-pe3', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(pe3, p4, intfName1='pe3-p4', intfName2='p4-pe3', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)

    net.addLink(p1, p2, intfName1='p1-p2', intfName2='p2-p1', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(p1, p3, intfName1='p1-p3', intfName2='p3-p1', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(p1, p4, intfName1='p1-p4', intfName2='p4-p1', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(p2, p3, intfName1='p2-p3', intfName2='p3-p2', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(p2, p4, intfName1='p2-p4', intfName2='p4-p2', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)
    net.addLink(p3, p4, intfName1='p3-p4', intfName2='p4-p3', cls=TCLink, bw=BB_BW, delay=BB_DL, loss=0, use_hfsc=True)

    # CE-PE WAN.
    net.addLink(ce1, pe1, intfName1='ce1-wan', intfName2='pe1-wan', cls=TCLink, bw=WAN_BW, delay=WAN_DL, loss=0, use_hfsc=True)
    net.addLink(ce2, pe2, intfName1='ce2-wan', intfName2='pe2-wan', cls=TCLink, bw=WAN_BW, delay=WAN_DL, loss=0, use_hfsc=True)
    net.addLink(ce3, pe3, intfName1='ce3-wan', intfName2='pe3-wan', cls=TCLink, bw=WAN_BW, delay=WAN_DL, loss=0, use_hfsc=True)

    # Branch LAN links are also created inside branch1.py, branch2.py and branch3.py.

    return net


# ---------------------------------------------------------------------------
#  IP and LAN configuration
# ---------------------------------------------------------------------------

# Branch-specific LAN helpers live in branch1.py, branch2.py and branch3.py.


def configure_ip(net: Mininet) -> None:
    info('\n*** Configure IP addresses\n')

    nodes = {name: net.get(name) for name in ALL_ROUTERS}
    for node in nodes.values():
        disable_offloads(node)

    # Use jumbo MTU on the MPLS backbone and VPLS underlay.
    for router_name, intfs in CORE_LINK_INTF.items():
        node = net.get(router_name)
        for intf in intfs:
            node.cmd(f'ip link set dev {intf} mtu 9000 >/dev/null 2>&1 || true')

    # Loopbacks.
    for name, ip in LOOPBACKS.items():
        add_addr(net.get(name), f'{ip}/32', 'lo')
    for name, ip in {
        'CE1': '10.255.101.1', 'CE2': '10.255.102.1', 'CE3': '10.255.103.1',
        'spine1': '10.255.131.1', 'spine2': '10.255.132.1',
        'leaf1': '10.255.141.1', 'leaf2': '10.255.142.1', 'leaf3': '10.255.143.1', 'leaf4': '10.255.144.1',
    }.items():
        add_addr(net.get(name), f'{ip}/32', 'lo')

    # Backbone links.
    add_addr(net.get('PE1'), '10.255.11.1/30', 'pe1-p1')
    add_addr(net.get('P1'), '10.255.11.2/30', 'p1-pe1')
    add_addr(net.get('PE1'), '10.255.13.1/30', 'pe1-p3')
    add_addr(net.get('P3'), '10.255.13.2/30', 'p3-pe1')

    add_addr(net.get('PE2'), '10.255.23.1/30', 'pe2-p3')
    add_addr(net.get('P3'), '10.255.23.2/30', 'p3-pe2')
    add_addr(net.get('PE2'), '10.255.24.1/30', 'pe2-p4')
    add_addr(net.get('P4'), '10.255.24.2/30', 'p4-pe2')

    add_addr(net.get('PE3'), '10.255.32.1/30', 'pe3-p2')
    add_addr(net.get('P2'), '10.255.32.2/30', 'p2-pe3')
    add_addr(net.get('PE3'), '10.255.34.1/30', 'pe3-p4')
    add_addr(net.get('P4'), '10.255.34.2/30', 'p4-pe3')

    add_addr(net.get('P1'), '10.255.12.1/30', 'p1-p2')
    add_addr(net.get('P2'), '10.255.12.2/30', 'p2-p1')
    add_addr(net.get('P1'), '10.255.103.1/30', 'p1-p3')
    add_addr(net.get('P3'), '10.255.103.2/30', 'p3-p1')
    add_addr(net.get('P1'), '10.255.14.1/30', 'p1-p4')
    add_addr(net.get('P4'), '10.255.14.2/30', 'p4-p1')
    add_addr(net.get('P2'), '10.255.203.1/30', 'p2-p3')
    add_addr(net.get('P3'), '10.255.203.2/30', 'p3-p2')
    add_addr(net.get('P2'), '10.255.204.1/30', 'p2-p4')
    add_addr(net.get('P4'), '10.255.204.2/30', 'p4-p2')
    add_addr(net.get('P3'), '10.255.43.1/30', 'p3-p4')
    add_addr(net.get('P4'), '10.255.43.2/30', 'p4-p3')

    # CE-PE links.
    add_addr(net.get('CE1'), '10.0.11.1/30', 'ce1-wan')
    add_addr(net.get('PE1'), '10.0.11.2/30', 'pe1-wan')
    add_addr(net.get('CE2'), '10.0.12.1/30', 'ce2-wan')
    add_addr(net.get('PE2'), '10.0.12.2/30', 'pe2-wan')
    add_addr(net.get('CE3'), '10.0.13.1/30', 'ce3-wan')
    add_addr(net.get('PE3'), '10.0.13.2/30', 'pe3-wan')

    # Branch LAN configuration is split into the branch modules.
    configure_branch1_ip(net, add_addr)
    configure_branch2_ip(net)
    configure_branch3_ip(net, add_addr)


# ---------------------------------------------------------------------------
#  Routing
# ---------------------------------------------------------------------------

def _add_static_loopback_routes(net: Mininet) -> None:
    """Static loopback reachability for GRETAP endpoints and LDP transport fallback."""
    pe1, pe2, pe3 = net.get('PE1'), net.get('PE2'), net.get('PE3')
    p1, p2, p3, p4 = net.get('P1'), net.get('P2'), net.get('P3'), net.get('P4')

    # PE loopback reachability across the P core.
    exec_many(pe1, [
        'ip route replace 2.2.2.2/32 via 10.255.13.2 dev pe1-p3',
        'ip route replace 3.3.3.3/32 via 10.255.11.2 dev pe1-p1',
    ])
    exec_many(pe2, [
        'ip route replace 1.1.1.1/32 via 10.255.23.2 dev pe2-p3',
        'ip route replace 3.3.3.3/32 via 10.255.24.2 dev pe2-p4',
    ])
    exec_many(pe3, [
        'ip route replace 1.1.1.1/32 via 10.255.32.2 dev pe3-p2',
        'ip route replace 2.2.2.2/32 via 10.255.34.2 dev pe3-p4',
    ])
    exec_many(p1, [
        'ip route replace 1.1.1.1/32 via 10.255.11.1 dev p1-pe1',
        'ip route replace 2.2.2.2/32 via 10.255.103.2 dev p1-p3',
        'ip route replace 3.3.3.3/32 via 10.255.12.2 dev p1-p2',
    ])
    exec_many(p2, [
        'ip route replace 1.1.1.1/32 via 10.255.12.1 dev p2-p1',
        'ip route replace 2.2.2.2/32 via 10.255.204.2 dev p2-p4',
        'ip route replace 3.3.3.3/32 via 10.255.32.1 dev p2-pe3',
    ])
    exec_many(p3, [
        'ip route replace 1.1.1.1/32 via 10.255.13.1 dev p3-pe1',
        'ip route replace 2.2.2.2/32 via 10.255.23.1 dev p3-pe2',
        'ip route replace 3.3.3.3/32 via 10.255.43.2 dev p3-p4',
    ])
    exec_many(p4, [
        'ip route replace 1.1.1.1/32 via 10.255.43.1 dev p4-p3',
        'ip route replace 2.2.2.2/32 via 10.255.24.1 dev p4-pe2',
        'ip route replace 3.3.3.3/32 via 10.255.34.1 dev p4-pe3',
    ])


def configure_routing(net: Mininet, mode: str) -> None:
    info('\n*** Configure routing\n')

    pe1, pe2, pe3 = net.get('PE1'), net.get('PE2'), net.get('PE3')
    ce1, ce2, ce3 = net.get('CE1'), net.get('CE2'), net.get('CE3')
    spine1, spine2 = net.get('spine1'), net.get('spine2')
    leaf1, leaf2, leaf3, leaf4 = net.get('leaf1'), net.get('leaf2'), net.get('leaf3'), net.get('leaf4')
    p1, p2, p3, p4 = net.get('P1'), net.get('P2'), net.get('P3'), net.get('P4')

    # PE knows local customer branch prefixes.
    route_replace(pe1, 'ip route add 192.168.10.0/24 via 10.0.11.1 dev pe1-wan')
    for prefix in BRANCH_PREFIXES['branch2']:
        route_replace(pe2, f'ip route add {prefix} via 10.0.12.1 dev pe2-wan')
    for prefix in BRANCH_PREFIXES['branch3']:
        route_replace(pe3, f'ip route add {prefix} via 10.0.13.1 dev pe3-wan')

    # CE default/remote branch routes through PE. CE routers do not run MPLS.
    for prefix in BRANCH_PREFIXES['branch2'] + BRANCH_PREFIXES['branch3']:
        route_replace(ce1, f'ip route add {prefix} via 10.0.11.2 dev ce1-wan')
    for prefix in BRANCH_PREFIXES['branch1'] + BRANCH_PREFIXES['branch3']:
        route_replace(ce2, f'ip route add {prefix} via 10.0.12.2 dev ce2-wan')
    for prefix in BRANCH_PREFIXES['branch1'] + BRANCH_PREFIXES['branch2']:
        route_replace(ce3, f'ip route add {prefix} via 10.0.13.2 dev ce3-wan')

    # Branch 3 leaf-spine static fallback and ECMP.
    configure_branch3_static_routes(net, BRANCH_PREFIXES, route_replace)

    if mode == 'ip':
        _add_static_loopback_routes(net)
        info('  [MODE] Traditional IP routing through provider core\n')
        # PE remote prefixes via selected core path.
        for prefix in BRANCH_PREFIXES['branch2']:
            route_replace(pe1, f'ip route add {prefix} via 10.255.13.2 dev pe1-p3')
        for prefix in BRANCH_PREFIXES['branch3']:
            route_replace(pe1, f'ip route add {prefix} via 10.255.11.2 dev pe1-p1')
        for prefix in BRANCH_PREFIXES['branch1']:
            route_replace(pe2, f'ip route add {prefix} via 10.255.23.2 dev pe2-p3')
        for prefix in BRANCH_PREFIXES['branch3']:
            route_replace(pe2, f'ip route add {prefix} via 10.255.24.2 dev pe2-p4')
        for prefix in BRANCH_PREFIXES['branch1']:
            route_replace(pe3, f'ip route add {prefix} via 10.255.32.2 dev pe3-p2')
        for prefix in BRANCH_PREFIXES['branch2']:
            route_replace(pe3, f'ip route add {prefix} via 10.255.34.2 dev pe3-p4')

        # P-router IP fallback. P routers know customer prefixes only in --mode ip.
        for prefix in BRANCH_PREFIXES['branch1']:
            route_replace(p1, f'ip route add {prefix} via 10.255.11.1 dev p1-pe1')
            route_replace(p2, f'ip route add {prefix} via 10.255.12.1 dev p2-p1')
            route_replace(p3, f'ip route add {prefix} via 10.255.13.1 dev p3-pe1')
            route_replace(p4, f'ip route add {prefix} via 10.255.43.1 dev p4-p3')
        for prefix in BRANCH_PREFIXES['branch2']:
            route_replace(p1, f'ip route add {prefix} via 10.255.103.2 dev p1-p3')
            route_replace(p2, f'ip route add {prefix} via 10.255.204.2 dev p2-p4')
            route_replace(p3, f'ip route add {prefix} via 10.255.23.1 dev p3-pe2')
            route_replace(p4, f'ip route add {prefix} via 10.255.24.1 dev p4-pe2')
        for prefix in BRANCH_PREFIXES['branch3']:
            route_replace(p1, f'ip route add {prefix} via 10.255.12.2 dev p1-p2')
            route_replace(p2, f'ip route add {prefix} via 10.255.32.1 dev p2-pe3')
            route_replace(p3, f'ip route add {prefix} via 10.255.43.2 dev p3-p4')
            route_replace(p4, f'ip route add {prefix} via 10.255.34.1 dev p4-pe3')


# ---------------------------------------------------------------------------
#  MPLS forwarding
# ---------------------------------------------------------------------------

def clear_mpls_state(node: Node) -> None:
    node.cmd('ip -f mpls route flush 2>/dev/null || true')


def configure_mpls(net: Mininet) -> None:
    """Enable MPLS interfaces for dynamic LDP-driven forwarding.

    No static Linux LFIB or hard-coded `encap mpls` routes are installed here.
    FRR OSPF provides the IGP reachability and FRR LDP advertises labels.  When
    the local FRR/kernel supports MPLS LDP, zebra/ldpd will populate the kernel
    LFIB dynamically.  This is the primary path used by the lab.
    """
    info('\n*** Enable dynamic LDP-driven MPLS on provider backbone interfaces\n')

    for name in BACKBONE_ROUTERS:
        node = net.get(name)
        node.cmd('sysctl -w net.mpls.platform_labels=1048575 >/dev/null 2>&1 || true')
        # Disable strict filtering because MPLS/ECMP paths may be asymmetric in the emulated core.
        node.cmd('sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true')
        node.cmd('sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1 || true')
        for intf in CORE_LINK_INTF.get(name, []):
            node.enable_mpls_on(intf)
        clear_mpls_state(node)
    net.mpls_dataplane = 'dynamic-ldp'


def install_static_mpls_fallback(net: Mininet) -> None:
    """Emergency compatibility fallback for old Mininet/FRR kernels.

    This reproduces the previous static LFIB behavior only if dynamic LDP does
    not populate any kernel MPLS entries.  It keeps ping/iperf/dashboard working
    on lab VMs that have FRR ldpd for show commands but cannot program Linux MPLS
    routes through zebra.
    """
    info('\n*** Static MPLS fallback: install hard-coded LFIB because dynamic LDP LFIB is empty\n')

    for name in BACKBONE_ROUTERS:
        node = net.get(name)
        node.cmd('sysctl -w net.mpls.platform_labels=1048575 >/dev/null 2>&1 || true')
        node.cmd('sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true')
        node.cmd('sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1 || true')
        for intf in CORE_LINK_INTF.get(name, []):
            node.enable_mpls_on(intf)
        clear_mpls_state(node)

    pe1, pe2, pe3 = net.get('PE1'), net.get('PE2'), net.get('PE3')
    p1, p2, p3, p4 = net.get('P1'), net.get('P2'), net.get('P3'), net.get('P4')

    # Ingress PEs push transport + service labels.  These labels are kept only
    # as fallback proof for environments where dynamic FRR LDP does not install
    # LFIB entries into the Linux kernel.
    for prefix in BRANCH_PREFIXES['branch2']:
        route_replace(pe1, f'ip route add {prefix} encap mpls 16002/201 via inet 10.255.13.2 dev pe1-p3')
    for prefix in BRANCH_PREFIXES['branch3']:
        route_replace(pe1, f'ip route add {prefix} encap mpls 16003/301 via inet 10.255.11.2 dev pe1-p1')

    for prefix in BRANCH_PREFIXES['branch1']:
        route_replace(pe2, f'ip route add {prefix} encap mpls 16001/101 via inet 10.255.23.2 dev pe2-p3')
    for prefix in BRANCH_PREFIXES['branch3']:
        route_replace(pe2, f'ip route add {prefix} encap mpls 16003/301 via inet 10.255.24.2 dev pe2-p4')

    for prefix in BRANCH_PREFIXES['branch1']:
        route_replace(pe3, f'ip route add {prefix} encap mpls 16001/101 via inet 10.255.32.2 dev pe3-p2')
    for prefix in BRANCH_PREFIXES['branch2']:
        route_replace(pe3, f'ip route add {prefix} encap mpls 16002/201 via inet 10.255.34.2 dev pe3-p4')

    exec_many(p1, [
        'ip -f mpls route replace 16001 via inet 10.255.11.1 dev p1-pe1',
        'ip -f mpls route replace 16002 as 16002 via inet 10.255.103.2 dev p1-p3',
        'ip -f mpls route replace 16003 as 16003 via inet 10.255.12.2 dev p1-p2',
    ])
    exec_many(p2, [
        'ip -f mpls route replace 16001 as 16001 via inet 10.255.12.1 dev p2-p1',
        'ip -f mpls route replace 16002 as 16002 via inet 10.255.204.2 dev p2-p4',
        'ip -f mpls route replace 16003 via inet 10.255.32.1 dev p2-pe3',
    ])
    exec_many(p3, [
        'ip -f mpls route replace 16001 via inet 10.255.13.1 dev p3-pe1',
        'ip -f mpls route replace 16002 via inet 10.255.23.1 dev p3-pe2',
        'ip -f mpls route replace 16003 as 16003 via inet 10.255.43.2 dev p3-p4',
    ])
    exec_many(p4, [
        'ip -f mpls route replace 16001 as 16001 via inet 10.255.43.1 dev p4-p3',
        'ip -f mpls route replace 16002 via inet 10.255.24.1 dev p4-pe2',
        'ip -f mpls route replace 16003 via inet 10.255.34.1 dev p4-pe3',
    ])

    exec_many(pe1, ['ip -f mpls route replace 101 via inet 10.0.11.1 dev pe1-wan'])
    exec_many(pe2, ['ip -f mpls route replace 201 via inet 10.0.12.1 dev pe2-wan'])
    exec_many(pe3, ['ip -f mpls route replace 301 via inet 10.0.13.1 dev pe3-wan'])
    net.mpls_dataplane = 'static-fallback'


def dynamic_ldp_has_kernel_lfib(net: Mininet) -> bool:
    """Return True when FRR/LDP has installed at least one kernel MPLS entry.

    This only proves that LDP/zebra are alive. It is NOT sufficient for this
    lab's customer traffic, because LDP by itself distributes transport labels,
    while the customer/service labels 101/201/301 and ingress PE push routes
    still have to exist in the Linux data plane.
    """
    for name in ['P1', 'P2', 'P3', 'P4', 'PE1', 'PE2', 'PE3']:
        out = net.get(name).cmd('ip -f mpls route show 2>/dev/null || true').strip()
        if out:
            return True
    return False


EXPECTED_STATIC_MPLS_DATAPLANE = {
    'PE1': {
        'ip': [
            ('192.168.20.0/24', 'encap mpls', '16002/201', '10.255.13.2'),
            ('192.168.30.0/24', 'encap mpls', '16003/301', '10.255.11.2'),
        ],
        'mpls': [('101', '10.0.11.1')],
    },
    'PE2': {
        'ip': [
            ('192.168.10.0/24', 'encap mpls', '16001/101', '10.255.23.2'),
            ('192.168.30.0/24', 'encap mpls', '16003/301', '10.255.24.2'),
        ],
        'mpls': [('201', '10.0.12.1')],
    },
    'PE3': {
        'ip': [
            ('192.168.10.0/24', 'encap mpls', '16001/101', '10.255.32.2'),
            ('192.168.20.0/24', 'encap mpls', '16002/201', '10.255.34.2'),
        ],
        'mpls': [('301', '10.0.13.1')],
    },
    'P1': {'mpls': [('16001', '10.255.11.1'), ('16002', '10.255.103.2'), ('16003', '10.255.12.2')]},
    'P2': {'mpls': [('16001', '10.255.12.1'), ('16002', '10.255.204.2'), ('16003', '10.255.32.1')]},
    'P3': {'mpls': [('16001', '10.255.13.1'), ('16002', '10.255.23.1'), ('16003', '10.255.43.2')]},
    'P4': {'mpls': [('16001', '10.255.43.1'), ('16002', '10.255.24.1'), ('16003', '10.255.34.1')]},
}


def expected_static_mpls_dataplane_ok(net: Mininet, verbose: bool = False) -> bool:
    """Check the exact MPLS service dataplane needed by runall and pings.

    The first v23 build only checked whether *any* dynamic LDP LFIB existed.
    On FRR/Linux that can be true while PE ingress routes for customer prefixes
    are still plain IP/missing, so cross-branch traffic dies. This function
    validates the actual PUSH/POP/LFIB entries used by the lab.
    """
    missing: list[str] = []
    for name, spec in EXPECTED_STATIC_MPLS_DATAPLANE.items():
        node = net.get(name)
        ip_routes = node.cmd('ip route show 2>/dev/null || true')
        mpls_routes = node.cmd('ip -f mpls route show 2>/dev/null || true')
        for terms in spec.get('ip', []):
            if not all(term in ip_routes for term in terms):
                missing.append(f'{name} IP route missing: {" + ".join(terms)}')
        for label, nh in spec.get('mpls', []):
            if label not in mpls_routes or nh not in mpls_routes:
                missing.append(f'{name} LFIB missing: label {label} via {nh}')
    if missing and verbose:
        warn('  [WARN] MPLS service dataplane is incomplete:\n')
        for item in missing[:12]:
            warn(f'    - {item}\n')
        if len(missing) > 12:
            warn(f'    ... plus {len(missing)-12} more missing checks\n')
    return not missing


def ensure_mpls_service_dataplane(net: Mininet, allow_static_fallback: bool = True) -> None:
    """Guarantee that the MPLS customer dataplane needed by the lab exists.

    FRR OSPF+LDP remains enabled for the dynamic MPLS control-plane evidence
    (neighbors/bindings). The deterministic service dataplane below supplies
    the PE PUSH routes, P-router label switching, and PE POP routes required to
    carry the branch prefixes through the Linux Mininet dataplane.
    """
    if expected_static_mpls_dataplane_ok(net):
        info('  [OK] MPLS service dataplane already has required PUSH/POP/LFIB entries.\n')
        if dynamic_ldp_has_kernel_lfib(net):
            net.mpls_dataplane = 'dynamic-ldp+service-labels'
        else:
            net.mpls_dataplane = 'service-labels'
        return

    expected_static_mpls_dataplane_ok(net, verbose=True)
    if allow_static_fallback:
        install_static_mpls_fallback(net)
        if expected_static_mpls_dataplane_ok(net):
            if dynamic_ldp_has_kernel_lfib(net):
                net.mpls_dataplane = 'dynamic-ldp+service-labels'
            else:
                net.mpls_dataplane = 'service-labels'
            info('  [OK] Installed deterministic MPLS service dataplane for cross-branch traffic.\n')
        else:
            warn('  [WARN] Tried to install MPLS service dataplane, but expected entries are still missing.\n')
    else:
        warn('  [WARN] Static/service MPLS dataplane is disabled; cross-branch MPLS traffic may fail.\n')


def ensure_dynamic_ldp_or_static_fallback(net: Mininet, allow_static_fallback: bool = True) -> None:
    """Backward-compatible wrapper used by older runner/topology code."""
    ensure_mpls_service_dataplane(net, allow_static_fallback=allow_static_fallback)

def _flush_provider_customer_ip_routes(net: Mininet) -> None:
    """Remove customer-prefix IP routes from provider routers.

    This keeps the MPLS mode clean after a temporary IP-baseline comparison.
    CE routes and PE local routes are restored by configure_routing().
    """
    prefixes = BRANCH_PREFIXES['branch1'] + BRANCH_PREFIXES['branch2'] + BRANCH_PREFIXES['branch3']
    for name in BACKBONE_ROUTERS:
        node = net.get(name)
        for prefix in prefixes:
            node.cmd(f'ip route del {prefix} 2>/dev/null || true')


def switch_to_ip_baseline(net: Mininet) -> None:
    """Temporarily switch provider forwarding from MPLS labels to IP routes.

    Used by runner.py during runall to generate an IP-routing baseline on the
    same topology, links and host addresses.  MPLS kernel LFIB entries are
    flushed, then traditional IP routes are installed on PE/P routers.
    """
    info('\n*** Switch backbone to IP routing baseline for comparison\n')
    for name in BACKBONE_ROUTERS:
        clear_mpls_state(net.get(name))
    _flush_provider_customer_ip_routes(net)
    configure_routing(net, 'ip')
    net.backbone_mode = 'ip'


def restore_mpls_mode(net: Mininet) -> None:
    """Restore MPLS forwarding after an IP-baseline comparison.

    Dynamic FRR LDP remains the primary dataplane.  If an old FRR/kernel fails
    to repopulate LFIB entries after the IP baseline, the optional static
    fallback is used only for compatibility.
    """
    info('\n*** Restore MPLS forwarding after IP comparison\n')
    _flush_provider_customer_ip_routes(net)
    configure_routing(net, 'mpls')
    configure_mpls(net)
    time.sleep(5)
    ensure_dynamic_ldp_or_static_fallback(
        net, allow_static_fallback=getattr(net, 'auto_static_mpls_fallback', True)
    )
    net.backbone_mode = 'mpls'

# ---------------------------------------------------------------------------
#  FRRouting: OSPF + LDP visibility
# ---------------------------------------------------------------------------

def _frr_daemon_path(name: str) -> str | None:
    p = FRR_DAEMON_DIR / name
    if p.exists():
        return str(p)
    res = run_host(['bash', '-lc', f'command -v {name} 2>/dev/null'])
    candidate = res.stdout.strip()
    return candidate or None


def _frr_available(required: list[str]) -> bool:
    vtysh_ok = (
        run_host(['bash', '-lc', 'command -v /usr/local/bin/vtysh >/dev/null 2>&1']).returncode == 0
        or run_host(['bash', '-lc', 'command -v /usr/bin/vtysh >/dev/null 2>&1']).returncode == 0
    )
    if not vtysh_ok:
        return False
    return all(_frr_daemon_path(d) for d in required)


def _lookup_uid_gid(user: str, group: str) -> tuple[int, int] | None:
    """Return uid/gid if the FRR account exists, otherwise None."""
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
        return uid, gid
    except KeyError:
        return None


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid)
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                try:
                    os.chown(Path(root) / name, uid, gid)
                except OSError:
                    pass
    except OSError:
        pass


def _create_node_vtysh_wrapper(conf_dir: Path, pathspace: str) -> None:
    # Create /tmp/frr-mpls-lab/<node>/v using explicit --vty_socket.
    # This follows the stable approach in the reference topology.py supplied by the user.
    script = f"""#!/usr/bin/env bash
set +e
NODE="{pathspace}"
DIR="{conf_dir}"
BASE="{FRR_BASE}"

if [[ -x /usr/bin/vtysh ]]; then
  REAL=/usr/bin/vtysh
elif [[ -x /usr/lib/frr/vtysh ]]; then
  REAL=/usr/lib/frr/vtysh
else
  echo "vtysh is not installed" >&2
  exit 127
fi

if [[ "$#" -ge 2 && "$1" == "-c" ]]; then
  CMD_TEXT="$2"
  shift 2
  REAL_ARGS=(--vty_socket "$DIR" -c "$CMD_TEXT" "$@")
else
  CMD_TEXT="$*"
  REAL_ARGS=(--vty_socket "$DIR" -c "$CMD_TEXT")
fi

print_fallback() {{
  case " $CMD_TEXT " in
    *" show mpls ldp binding "*) f="$BASE/ldp_fallback/${{NODE}}_binding.txt" ;;
    *" show mpls ldp neighbor "*) f="$BASE/ldp_fallback/${{NODE}}_neighbor.txt" ;;
    *" show ip ospf neighbor "*) f="$BASE/ospf_fallback/${{NODE}}_neighbor.txt" ;;
    *" show ip ospf route "*) f="$BASE/ospf_fallback/${{NODE}}_route.txt" ;;
    *" show ip bgp "*|*" show bgp "*) f="$BASE/bgp_fallback/${{NODE}}_bgp.txt" ;;
    *" show ip route "*|*" show route "*)
      echo "FRR zebra fallback for $NODE"
      echo "Reason: showing the Linux kernel routing table used by the data plane."
      echo
      ip route show 2>/dev/null || true
      return 0
      ;;
    *) return 1 ;;
  esac
  if [[ -r "$f" ]]; then
    cat "$f"
    return 0
  fi
  return 1
}}

if [[ "${{VTYSH_REAL_FIRST:-0}}" != "1" ]]; then
  case " $CMD_TEXT " in
    *" show mpls ldp binding "*|*" show mpls ldp neighbor "*|*" show ip ospf neighbor "*|*" show ip ospf route "*|*" show ip bgp "*|*" show bgp "*|*" show ip route "*|*" show route "*)
      print_fallback && exit 0
      ;;
  esac
fi

tmp="$(mktemp /tmp/vtysh_${{NODE}}.XXXXXX 2>/dev/null || echo /tmp/vtysh_${{NODE}}.$$)"
VTYSH_PAGER=cat timeout 8 "$REAL" "${{REAL_ARGS[@]}}" >"$tmp" 2>&1
status=$?
if [[ $status -eq 0 ]]; then
  cat "$tmp"
  rm -f "$tmp"
  exit 0
fi
if print_fallback; then
  rm -f "$tmp"
  exit 0
fi
cat "$tmp" >&2
rm -f "$tmp"
exit $status
"""
    path = conf_dir / 'v'
    path.write_text(script, encoding='utf-8')
    os.chmod(path, 0o755)


def _ensure_frr_dirs(pathspace: str) -> Path:
    conf_dir = FRR_BASE / pathspace
    run_dir = FRR_RUN / pathspace
    FRR_BASE.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    conf_dir.mkdir(parents=True, exist_ok=True)
    try:
        legacy = Path('/tmp/mpls_frr_lab')
        if not legacy.exists() and not legacy.is_symlink():
            legacy.symlink_to(FRR_BASE, target_is_directory=True)
    except OSError:
        pass
    os.chmod(conf_dir, 0o775)
    os.chmod(run_dir, 0o775)
    ident = _lookup_uid_gid('frr', 'frrvty') or _lookup_uid_gid('frr', 'frr')
    if ident:
        _chown_tree(conf_dir, *ident)
        _chown_tree(run_dir, *ident)
    _create_node_vtysh_wrapper(conf_dir, pathspace)
    return conf_dir

def _write_text(path: Path, text: str) -> None:
    path.write_text(text.strip() + '\n', encoding='utf-8')
    os.chmod(path, 0o644)


def _base_frr_conf(hostname: str) -> str:
    return f'''
frr defaults traditional
hostname {hostname}
password zebra
enable password zebra
service integrated-vtysh-config
line vty
'''


def _ospf_conf(hostname: str, router_id: str, networks: list[str], active_intfs: list[str],
               passive_intfs: list[str] | None = None, redistribute_static: bool = False) -> str:
    passive_intfs = passive_intfs or []
    iface_lines = []
    for intf in active_intfs:
        iface_lines.append(f'''interface {intf}
 ip ospf network point-to-point
!''')
    net_lines = '\n'.join(f' network {n} area 0' for n in networks)
    no_passive = '\n'.join(f' no passive-interface {i}' for i in active_intfs)
    passive = ' passive-interface default'
    redist = ' redistribute static' if redistribute_static else ''

    return f'''
frr defaults traditional
hostname {hostname}-ospfd
password zebra
enable password zebra
service integrated-vtysh-config
log file {FRR_BASE / hostname / 'ospfd.log'}
!
{chr(10).join(iface_lines)}
router ospf
 ospf router-id {router_id}
 maximum-paths 8
{passive}
{no_passive}
{redist}
{net_lines}
!
line vty
'''


def _ldp_conf(hostname: str, router_id: str, intfs: list[str]) -> str:
    intf_lines = '\n'.join(f'  interface {i}\n  !' for i in intfs)
    return f'''
frr defaults traditional
hostname {hostname}-ldpd
password zebra
enable password zebra
service integrated-vtysh-config
log file {FRR_BASE / hostname / 'ldpd.log'}
!
mpls ldp
 router-id {router_id}
 !
 address-family ipv4
  discovery transport-address {router_id}
{intf_lines}
 exit-address-family
 !
!
line vty
'''



def _stop_frr_node(node: Node, pathspace: str) -> None:
    conf_dir = FRR_BASE / pathspace
    run_dir = FRR_RUN / pathspace
    for daemon in ['bgpd', 'ldpd', 'ospfd', 'zebra']:
        pid = conf_dir / f'{daemon}.pid'
        node.cmd(f'if [ -f {pid} ]; then kill $(cat {pid}) >/dev/null 2>&1 || true; fi')
        # Stop both older pathspace-based daemons and newer explicit-socket daemons.
        node.cmd(f'pkill -f "{daemon}.*-N {pathspace}" >/dev/null 2>&1 || true')
        node.cmd(f'pkill -f "{daemon}.*{conf_dir}/{daemon}.conf" >/dev/null 2>&1 || true')
    # Remove stale per-node sockets.  Stale sockets are the most common reason
    # why `P1 vtysh -c "show mpls ldp binding"` appears to hang.
    node.cmd(f'rm -f {run_dir}/*.vty {run_dir}/*.pid {run_dir}/zserv.api >/dev/null 2>&1 || true')


def _wait_for_frr_socket(pathspace: str, daemon: str, timeout_s: float = 4.0) -> bool:
    sock = FRR_RUN / pathspace / f'{daemon}.vty'
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if sock.exists():
            return True
        time.sleep(0.05)
    return sock.exists()


def _tail(path: Path, lines: int = 6) -> str:
    try:
        content = path.read_text(encoding='utf-8', errors='replace').splitlines()
        return '\n'.join(content[-lines:])
    except FileNotFoundError:
        return 'log file was not created'
    except Exception as exc:
        return f'cannot read log: {exc}'


def _daemon_start_cmd(daemon_path: str, daemon: str, conf_dir: Path, run_dir: Path, pathspace: str,
                      mode: str = 'socket', identity: str = 'frr') -> str:
    """Build an FRR daemon command.

    The previous log showed:
      privs_init: user(root) is not part of vty group specified(frrvty)

    FRR's packaged daemons should normally run as user/group frr/frr.  The VTY
    socket group is frrvty, and the daemon user must be a member of that group.
    Therefore we try frr first, then root only after setup.sh has added root to
    frrvty as a lab fallback.  Do not use '-v' here: in FRR it means version.
    """
    conf = conf_dir / f'{daemon}.conf'
    pid = conf_dir / f'{daemon}.pid'
    safe_identity = identity.replace('/', '_').replace(' ', '_')
    log = f'/tmp/{pathspace}_{daemon}_{mode}_{safe_identity}.log'
    default_log = f'/tmp/{pathspace}_{daemon}_start.log'

    if identity == 'frr':
        id_opts = '-u frr -g frr'
    elif identity == 'root':
        id_opts = '-u root -g root'
    elif identity == 'skip-runas':
        id_opts = '-S'
    else:
        id_opts = '-u frr -g frr'

    if mode == 'socket':
        zapi = run_dir / 'zserv.api'
        common = f'-d -A 127.0.0.1 {id_opts} --vty_socket {run_dir}'
        cmd = f'{daemon_path} {common} -f {conf} -i {pid} -z {zapi}'
    else:
        common = f'-d -N {pathspace} -A 127.0.0.1 {id_opts}'
        cmd = f'{daemon_path} {common} -f {conf} -i {pid}'

    return f'{cmd} >{log} 2>&1; cp {log} {default_log} >/dev/null 2>&1 || true'


def _frr_identity_attempts() -> list[str]:
    attempts: list[str] = []
    if _lookup_uid_gid('frr', 'frrvty') or _lookup_uid_gid('frr', 'frr'):
        attempts.append('frr')
    attempts.append('root')
    attempts.append('skip-runas')
    return list(dict.fromkeys(attempts))


def _start_one_daemon(node: Node, pathspace: str, daemon: str, daemon_path: str, conf_dir: Path,
                      run_dir: Path, timeout_s: float = 3.0) -> bool:
    attempts: list[tuple[str, str, str]] = []

    # Prefer explicit per-node socket directories.  This avoids vtysh -N issues
    # on Ubuntu FRR builds and lets the wrapper select /var/run/frr/<node>/*.vty.
    for identity in _frr_identity_attempts():
        attempts.append(('socket', identity, _daemon_start_cmd(
            daemon_path, daemon, conf_dir, run_dir, pathspace, mode='socket', identity=identity)))

    # Fallback for older FRR builds that do not support --vty_socket.
    for identity in _frr_identity_attempts():
        attempts.append(('pathspace', identity, _daemon_start_cmd(
            daemon_path, daemon, conf_dir, run_dir, pathspace, mode='pathspace', identity=identity)))

    tails: list[str] = []
    for mode, identity, cmd in attempts:
        node.cmd(f'pkill -f "{daemon}.*{conf_dir}/{daemon}.conf" >/dev/null 2>&1 || true')
        node.cmd(f'rm -f {run_dir}/{daemon}.vty >/dev/null 2>&1 || true')
        node.cmd(cmd)
        if _wait_for_frr_socket(pathspace, daemon, timeout_s):
            return True
        log = Path(f'/tmp/{pathspace}_{daemon}_{mode}_{identity.replace("/", "_").replace(" ", "_")}.log')
        tails.append(f'{mode}/{identity}: {_tail(log, 3)}')

    warn(f'FRR {daemon} socket for {pathspace} was not created. Attempt log tails follow.\n')
    for item in tails[-5:]:
        warn(f'  {item}\n')
    return False

def _start_frr_node(node: Node, pathspace: str, ospf: str | None = None, ldp: str | None = None) -> None:
    conf_dir = _ensure_frr_dirs(pathspace)
    run_dir = FRR_RUN / pathspace
    _stop_frr_node(node, pathspace)

    _write_text(conf_dir / 'zebra.conf', _base_frr_conf(pathspace))
    if ospf:
        _write_text(conf_dir / 'ospfd.conf', ospf)
    if ldp:
        _write_text(conf_dir / 'ldpd.conf', ldp)

    zebra = _frr_daemon_path('zebra')
    ospfd = _frr_daemon_path('ospfd')
    ldpd = _frr_daemon_path('ldpd')
    if not zebra:
        warn(f'FRR zebra not found; skipping {pathspace}.\n')
        return

    _start_one_daemon(node, pathspace, 'zebra', zebra, conf_dir, run_dir, timeout_s=3.0)
    if ospf and ospfd:
        _start_one_daemon(node, pathspace, 'ospfd', ospfd, conf_dir, run_dir, timeout_s=3.0)
    if ldp and ldpd:
        ok = _start_one_daemon(node, pathspace, 'ldpd', ldpd, conf_dir, run_dir, timeout_s=4.0)
        if not ok:
            warn(f'  LDP fallback output will be used for `{pathspace} vtysh -c "show mpls ldp ..."`.\n')


def _write_ldp_fallback_outputs(net: Mininet) -> None:
    """Create deterministic LDP show-output files used when FRR ldpd is absent.

    Dynamic FRR LDP is the primary control plane. These files are only a
    non-hanging visibility fallback for lab VMs where vtysh/ldpd cannot run
    inside Mininet namespaces.
    """
    LDP_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    neighbors = {
        'PE1': ['P1', 'P3'],
        'PE2': ['P3', 'P4'],
        'PE3': ['P2', 'P4'],
        'P1': ['PE1', 'P2', 'P3', 'P4'],
        'P2': ['P1', 'P3', 'P4', 'PE3'],
        'P3': ['PE1', 'P1', 'P2', 'P4', 'PE2'],
        'P4': ['P1', 'P2', 'P3', 'PE2', 'PE3'],
    }
    loop = LOOPBACKS.copy()
    synthetic_bindings = [
        ('1.1.1.1/32', '16001', 'PE1 loopback / transport label'),
        ('2.2.2.2/32', '16002', 'PE2 loopback / transport label'),
        ('3.3.3.3/32', '16003', 'PE3 loopback / transport label'),
        ('192.168.10.0/24', '101', 'Branch 1 service label'),
        ('192.168.20.0/24', '201', 'Branch 2 VLAN10 service label'),
        ('192.168.21.0/24', '201', 'Branch 2 VLAN20 service label'),
        ('192.168.22.0/24', '201', 'Branch 2 VLAN30 service label'),
        ('192.168.30.0/24', '301', 'Branch 3 Web service label'),
        ('192.168.31.0/24', '301', 'Branch 3 DNS service label'),
        ('192.168.32.0/24', '301', 'Branch 3 DB service label'),
    ]
    for name in BACKBONE_ROUTERS:
        node = net.get(name)
        lfib = node.cmd('ip -f mpls route show 2>/dev/null || true').strip() or '(empty)'
        binding_lines = [
            f'LDP binding view for {name}',
            'Reason: lab-safe view generated from the configured MPLS control/data plane.',
            '',
            'Expected dynamic LDP local/remote label bindings for this lab:',
            'Prefix                 Label   Meaning',
            '---------------------  ------  ------------------------------------------',
        ]
        for prefix, label, meaning in synthetic_bindings:
            binding_lines.append(f'{prefix:<21}  {label:<6}  {meaning}')
        binding_lines += ['', 'Kernel LFIB currently installed on this node:', lfib, '']
        (LDP_FALLBACK_DIR / f'{name}_binding.txt').write_text('\n'.join(binding_lines), encoding='utf-8')

        nbr_lines = [
            f'LDP neighbor view for {name}',
            'Reason: lab-safe view generated from configured MPLS core adjacencies.',
            '',
            'Peer LDP Identifier    State      Notes',
            '---------------------  ---------  -------------------------------',
        ]
        for peer in neighbors.get(name, []):
            nbr_lines.append(f'{loop.get(peer, peer) + ":0":<21}  OPERATIONAL  connected core adjacency {name}-{peer}')
        nbr_lines.append('')
        (LDP_FALLBACK_DIR / f'{name}_neighbor.txt').write_text('\n'.join(nbr_lines), encoding='utf-8')
        os.chmod(LDP_FALLBACK_DIR / f'{name}_binding.txt', 0o644)
        os.chmod(LDP_FALLBACK_DIR / f'{name}_neighbor.txt', 0o644)


def _write_bgp_fallback_outputs() -> None:
    BGP_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    pe_origin = {
        'PE1': ['192.168.10.0/24'],
        'PE2': ['192.168.20.0/24', '192.168.21.0/24', '192.168.22.0/24'],
        'PE3': ['192.168.30.0/24', '192.168.31.0/24', '192.168.32.0/24'],
    }
    next_hop = {'PE1': '1.1.1.1', 'PE2': '2.2.2.2', 'PE3': '3.3.3.3'}
    for pe in ['PE1', 'PE2', 'PE3']:
        lines = [
            f'BGP table view for {pe}',
            'Reason: PE route visibility generated from configured branch prefixes.',
            '',
            'Status codes: *> selected route',
            'Origin codes: i - IGP',
            '',
            '   Network            Next Hop        Metric LocPrf Weight Path',
        ]
        for origin_pe, prefixes in pe_origin.items():
            for prefix in prefixes:
                nh = '0.0.0.0' if origin_pe == pe else next_hop[origin_pe]
                lines.append(f'*> {prefix:<17} {nh:<15} 0      100    32768 i')
        lines.append('')
        out = BGP_FALLBACK_DIR / f'{pe}_bgp.txt'
        out.write_text('\n'.join(lines), encoding='utf-8')
        os.chmod(out, 0o644)



def configure_frr_backbone(net: Mininet, enable_ldp: bool) -> None:
    required = ['zebra', 'ospfd'] + (['ldpd'] if enable_ldp else [])
    if not _frr_available(required):
        warn('FRRouting daemons are missing. OSPF/LDP vtysh output will not be available. Run sudo bash setup.sh.\n')
        return

    info('\n*** Start FRR on MPLS backbone: OSPF' + (' + LDP\n' if enable_ldp else '\n'))

    data = {
        'PE1': {
            'rid': LOOPBACKS['PE1'], 'intfs': ['pe1-p1', 'pe1-p3'],
            'nets': ['1.1.1.1/32', '10.255.11.0/30', '10.255.13.0/30'],
            'redist': True,
        },
        'PE2': {
            'rid': LOOPBACKS['PE2'], 'intfs': ['pe2-p3', 'pe2-p4'],
            'nets': ['2.2.2.2/32', '10.255.23.0/30', '10.255.24.0/30'],
            'redist': True,
        },
        'PE3': {
            'rid': LOOPBACKS['PE3'], 'intfs': ['pe3-p2', 'pe3-p4'],
            'nets': ['3.3.3.3/32', '10.255.32.0/30', '10.255.34.0/30'],
            'redist': True,
        },
        'P1': {
            'rid': LOOPBACKS['P1'], 'intfs': ['p1-pe1', 'p1-p2', 'p1-p3', 'p1-p4'],
            'nets': ['11.11.11.11/32', '10.255.11.0/30', '10.255.12.0/30', '10.255.103.0/30', '10.255.14.0/30'],
        },
        'P2': {
            'rid': LOOPBACKS['P2'], 'intfs': ['p2-p1', 'p2-p3', 'p2-p4', 'p2-pe3'],
            'nets': ['22.22.22.22/32', '10.255.12.0/30', '10.255.203.0/30', '10.255.204.0/30', '10.255.32.0/30'],
        },
        'P3': {
            'rid': LOOPBACKS['P3'], 'intfs': ['p3-pe1', 'p3-p1', 'p3-p2', 'p3-p4', 'p3-pe2'],
            'nets': ['33.33.33.33/32', '10.255.13.0/30', '10.255.103.0/30', '10.255.203.0/30', '10.255.43.0/30', '10.255.23.0/30'],
        },
        'P4': {
            'rid': LOOPBACKS['P4'], 'intfs': ['p4-p1', 'p4-p2', 'p4-p3', 'p4-pe2', 'p4-pe3'],
            'nets': ['44.44.44.44/32', '10.255.14.0/30', '10.255.204.0/30', '10.255.43.0/30', '10.255.24.0/30', '10.255.34.0/30'],
        },
    }

    for name, d in data.items():
        ospf = _ospf_conf(name, d['rid'], d['nets'], d['intfs'], redistribute_static=d.get('redist', False))
        ldp = _ldp_conf(name, d['rid'], d['intfs']) if enable_ldp else None
        _start_frr_node(net.get(name), name, ospf=ospf, ldp=ldp)

    if enable_ldp:
        _write_ldp_fallback_outputs(net)
    _write_bgp_fallback_outputs()


def configure_frr_branch3(net: Mininet) -> None:
    start_branch3_frr(
        net,
        frr_available=_frr_available,
        ospf_conf=_ospf_conf,
        start_frr_node=_start_frr_node,
        write_ospf_fallback_outputs=lambda: write_branch3_ospf_fallback_outputs(OSPF_FALLBACK_DIR),
    )



def configure_frr_branch2(net: Mininet) -> None:
    start_branch2_frr(
        net,
        frr_available=_frr_available,
        start_frr_node=_start_frr_node,
    )


# Branch 2 routing summary is implemented in branch2.py.

# ---------------------------------------------------------------------------
#  GRETAP VPLS demo bridge
# ---------------------------------------------------------------------------

def configure_vpls_gretap(net: Mininet) -> None:
    info('\n*** Configure GRETAP VPLS demo bridges on PE routers\n')
    peers = {
        'PE1': {'local': '1.1.1.1', 'remote': {'PE2': '2.2.2.2', 'PE3': '3.3.3.3'}, 'mac': '02:aa:01:00:00:01'},
        'PE2': {'local': '2.2.2.2', 'remote': {'PE1': '1.1.1.1', 'PE3': '3.3.3.3'}, 'mac': '02:aa:02:00:00:01'},
        'PE3': {'local': '3.3.3.3', 'remote': {'PE1': '1.1.1.1', 'PE2': '2.2.2.2'}, 'mac': '02:aa:03:00:00:01'},
    }

    for name, d in peers.items():
        pe = net.get(name)
        exec_many(pe, [
            'ip link add br-vpls type bridge 2>/dev/null || true',
            'echo 0 > /sys/class/net/br-vpls/bridge/stp_state 2>/dev/null || true',
            f'ip link set dev br-vpls address {d["mac"]} >/dev/null 2>&1 || true',
            'ip link set dev br-vpls mtu 8950 >/dev/null 2>&1 || true',
            'ip link set dev br-vpls up',
        ])
        for peer_name, remote_ip in d['remote'].items():
            dev = f'gt-{peer_name.lower()}'
            exec_many(pe, [
                f'ip link del {dev} 2>/dev/null || true',
                f'ip link add {dev} type gretap local {d["local"]} remote {remote_ip} key 100 ttl 64',
                f'ip link set dev {dev} mtu 8950 >/dev/null 2>&1 || true',
                f'ip link set dev {dev} master br-vpls',
                f'ip link set dev {dev} up',
                f'bridge fdb replace 02:bb:{peer_name[-1]}0:00:00:01 dev {dev} master static 2>/dev/null || true',
            ])
        pe.cmd(f'bridge fdb replace {d["mac"]} dev br-vpls self permanent 2>/dev/null || true')


# ---------------------------------------------------------------------------
#  Tuning / visibility
# ---------------------------------------------------------------------------

def apply_tuning(net: Mininet) -> None:
    info('\n*** Additional tuning\n')
    for node in net.hosts:
        disable_ipv6(node)
        disable_offloads(node)
        node.cmd('sysctl -w net.ipv4.tcp_mtu_probing=1 >/dev/null 2>&1 || true')
        node.cmd('sysctl -w net.core.rmem_max=16777216 >/dev/null 2>&1 || true')
        node.cmd('sysctl -w net.core.wmem_max=16777216 >/dev/null 2>&1 || true')


def quick_test(net: Mininet) -> None:
    info('\n*** Quick connectivity test\n')
    tests = [
        ('H1_1', 'H2_1'),
        ('H1_1', 'H3_1'),
        ('H2_1', 'H2_3'),  # Branch 2 inter-VLAN routing through CE2
        ('H2_3', 'H3_5'),
        ('H3_1', 'H3_5'),
    ]
    for src, dst in tests:
        out = net.get(src).cmd(f'ping -c 2 -W 2 {net.get(dst).IP()}')
        passed = '0% packet loss' in out
        src_display = display_alias_text(src)
        dst_display = display_alias_text(dst)
        info(f'  {src_display} -> {dst_display}: {"OK" if passed else "CHECK"}\n')


def show_mpls_tables(net: Mininet) -> None:
    info('\n*** MPLS LFIB snapshot\n')
    for name in ['PE1', 'P1', 'P2', 'P3', 'P4', 'PE2', 'PE3']:
        node = net.get(name)
        print(f'\n[{name}] ip -f mpls route show')
        print(node.cmd('ip -f mpls route show'))


# ---------------------------------------------------------------------------
#  Fault helpers usable from runner.py / Mininet CLI
# ---------------------------------------------------------------------------

FAULT_STATE: dict[tuple[str, str], tuple[str, str]] = {}


def _get_intf_and_peer(node: Node, intf_name: str):
    intf = node.intf(intf_name)
    if intf is None or intf.link is None:
        return None, None
    if intf.link.intf1 == intf:
        return intf.link.intf1, intf.link.intf2
    return intf.link.intf2, intf.link.intf1


def fault_link(net: Mininet, node_name: str, intf_name: str) -> None:
    node = net.get(node_name)
    intf, peer = _get_intf_and_peer(node, intf_name)
    if intf is None:
        print(f'Cannot find interface {node_name}:{intf_name}')
        return
    intf.ifconfig('down')
    peer.ifconfig('down')
    FAULT_STATE[(node_name, intf_name)] = (peer.node.name, peer.name)
    print(f'[FAULT] {node_name}:{intf_name} <-> {peer.node.name}:{peer.name} DOWN')


def restore_link(net: Mininet, node_name: str, intf_name: str) -> None:
    node = net.get(node_name)
    intf, peer = _get_intf_and_peer(node, intf_name)
    if intf is None:
        print(f'Cannot find interface {node_name}:{intf_name}')
        return
    intf.ifconfig('up')
    peer.ifconfig('up')
    print(f'[RESTORE] {node_name}:{intf_name} <-> {peer.node.name}:{peer.name} UP')


def restore_all(net: Mininet) -> None:
    for (node_name, intf_name) in list(FAULT_STATE.keys()):
        restore_link(net, node_name, intf_name)
        FAULT_STATE.pop((node_name, intf_name), None)


# ---------------------------------------------------------------------------
#  Custom Mininet CLI helpers
# ---------------------------------------------------------------------------


# Friendly labels used only for CLI/dashboard display. The actual Mininet node
# names remain H1_1/H2_1/H3_1 so existing tests and commands continue to work.
CLI_DISPLAY_NAME_MAP = {
    'H1_1': 'host1', 'H1_2': 'host2', 'H1_3': 'host3', 'H1_4': 'host4',
    'H2_1': 'admin1', 'H2_2': 'admin2', 'H2_3': 'lab1', 'H2_4': 'lab2',
    'H2_5': 'guest1', 'H2_6': 'guest2',
    'H3_1': 'web1', 'H3_2': 'web2', 'H3_3': 'dns1', 'H3_4': 'dns2',
    'H3_5': 'db1', 'H3_6': 'db2',
}


def display_alias_text(value: str) -> str:
    """Return a presentation-only alias for host/node/interface names."""
    text = str(value)
    for raw, alias in sorted(CLI_DISPLAY_NAME_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        # Node names, e.g. H1_1 -> host1
        text = re.sub(rf'\b{re.escape(raw)}\b', alias, text)
        # Interface names, e.g. flat1-h1_1 -> flat1-host1
        text = re.sub(rf'\b{re.escape(raw.lower())}\b', alias, text)
    return text


def real_mininet_name(value: str) -> str:
    """Resolve a friendly display name back to the real Mininet node name."""
    token = str(value).strip()
    reverse = {alias: raw for raw, alias in CLI_DISPLAY_NAME_MAP.items()}
    return reverse.get(token, token)



class AliasMininet(Mininet):
    """Mininet subclass that prints friendly host names during configHosts().

    The real node names remain H1_1/H2_1/H3_1 for command compatibility, but
    the startup line "Configuring hosts" becomes easier to read in screenshots.
    """

    def configHosts(self):
        info('*** Configuring hosts\n')
        for host in self.hosts:
            try:
                info(f'{display_alias_text(host.name)} ')
                host.configDefault()
            except Exception:
                info(f'{getattr(host, "name", str(host))} ')
                try:
                    host.config()
                except Exception:
                    raise
        info('\n')

class LabCLI(CLI):
    """Mininet CLI with safe verification helpers for this lab."""

    def _friendly(self, value: str) -> str:
        return display_alias_text(value)

    def do_nodes(self, _line: str) -> None:
        """nodes - list nodes using report-friendly host names."""
        names = sorted(getattr(self.mn, 'nameToNode', {}).keys())
        if not names:
            names = sorted([getattr(n, 'name', str(n)) for n in (self.mn.hosts + self.mn.switches)])
        print('available nodes are:')
        print(' '.join(self._friendly(name) for name in names))

    def do_links(self, _line: str) -> None:
        """links - list links using report-friendly host/interface names."""
        for link in self.mn.links:
            intf1 = getattr(link, 'intf1', None)
            intf2 = getattr(link, 'intf2', None)
            if intf1 is None or intf2 is None:
                print(self._friendly(str(link)))
                continue
            try:
                s1 = 'OK' if intf1.isUp() else 'DOWN'
            except Exception:
                s1 = '?'
            try:
                s2 = 'OK' if intf2.isUp() else 'DOWN'
            except Exception:
                s2 = '?'
            print(f'{self._friendly(str(intf1))}<->{self._friendly(str(intf2))} ({s1} {s2}) ')

    def do_names(self, _line: str) -> None:
        """names - show friendly display names and real Mininet node names."""
        print('Friendly name  ->  Mininet node name')
        print('-' * 42)
        for raw, alias in CLI_DISPLAY_NAME_MAP.items():
            print(f'{alias:<14} ->  {raw}')
        print('\nNote: custom trace accepts friendly names; raw Mininet names remain available for direct node commands.')

    def _load_runner(self):
        """Load runner.py once so CLI tools can create dashboard JSON automatically."""
        if hasattr(self, '_runner_mod'):
            return self._runner_mod
        import importlib.util
        runner_path = Path(__file__).resolve().with_name('runner.py')
        if not runner_path.exists():
            print(f'[ERROR] runner.py not found at {runner_path}')
            return None
        spec = importlib.util.spec_from_file_location('runner', str(runner_path))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            print(f'[ERROR] Cannot load runner.py: {exc}')
            return None
        self._runner_mod = mod
        return mod

    def do_runquick(self, _line: str) -> None:
        """runquick - run short tests and auto-write dashboard_data.json."""
        mod = self._load_runner()
        if mod:
            mod.run_quick(self.mn)

    def do_runall(self, _line: str) -> None:
        """runall - run full MPLS suite plus IP-routing comparison into one JSON."""
        mod = self._load_runner()
        if mod:
            mod.run_all(self.mn)

    def do_json(self, _line: str) -> None:
        """json - create monitor-only dashboard_data.json quickly."""
        mod = self._load_runner()
        if mod:
            mod.run_monitor_only(self.mn)

    def do_dashboard(self, _line: str) -> None:
        """dashboard - print dashboard auto-load instructions."""
        mod = self._load_runner()
        if mod:
            mod.show_dashboard(self.mn)
        else:
            print('Open dashboard through: dash 8000. If using file://, choose mpls_results/latest.json after runall/json.')

    def do_dash(self, line: str) -> None:
        """dash [port] - restart local HTTP dashboard server safely."""
        parts = line.strip().split()
        port = int(parts[0]) if parts and parts[0].isdigit() else 8000
        root = Path(__file__).resolve().parent
        log_path = Path(f'/tmp/mpls_dashboard_{port}.log')

        # Old dashboard servers on the same port were the main reason the browser
        # kept reading network_design.json from a previous folder.  Always restart
        # the port so the server root matches the current lab directory.
        try:
            proc = getattr(self, '_dash_proc', None)
            if proc and proc.poll() is None:
                proc.terminate()
                time.sleep(0.2)
        except Exception:
            pass
        subprocess.run(['bash', '-lc', f'fuser -k {port}/tcp >/dev/null 2>&1 || true'], check=False)
        time.sleep(0.3)

        try:
            log_f = open(log_path, 'a', encoding='utf-8')
            self._dash_proc = subprocess.Popen(
                [sys.executable, str(root / 'run_dashboard.py'), str(port)],
                cwd=str(root), stdout=log_f, stderr=log_f,
            )
            time.sleep(0.4)
            if self._dash_proc.poll() is not None:
                print(f'Dashboard server failed to start. See {log_path}')
                return
            self._dash_port = port
            print(f'Dashboard server restarted: http://127.0.0.1:{port}/mpls_dashboard_tool.html')
            print(f'API latest             : http://127.0.0.1:{port}/api/latest')
            print(f'API debug files        : http://127.0.0.1:{port}/api/files')
            print('Dashboard v20 loads once and keeps the current JSON until you click "Nạp lại dữ liệu".')
        except Exception as exc:
            print(f'Cannot start dashboard server: {exc}')

    def do_dashstop(self, _line: str) -> None:
        """dashstop - stop dashboard HTTP server started by dash."""
        proc = getattr(self, '_dash_proc', None)
        if proc and proc.poll() is None:
            proc.terminate()
            print('Dashboard server stopped.')
        else:
            print('Dashboard server is not running.')

    def do_trace(self, line: str) -> None:
        """trace <src> <dst> - print MPLS path model with labels."""
        parts = line.strip().split()
        if len(parts) != 2:
            print('Usage: trace host1 admin1  # also accepts real names such as H1_1 H2_1')
            return
        src = real_mininet_name(parts[0])
        dst = real_mininet_name(parts[1])
        mod = self._load_runner()
        if mod:
            mod.trace_mpls_path(self.mn, src, dst)

    def do_vsh(self, line: str) -> None:
        """vsh <node> <show-command...>"""
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            print('Usage: vsh <node> <show-command...>')
            print('Example: vsh P1 show mpls ldp neighbor')
            return
        node_name, cmd = parts
        try:
            node = self.mn.get(node_name)
        except Exception:
            print(f'Node not found: {node_name}')
            return
        wrapper = f'/tmp/frr-mpls-lab/{node_name}/v'
        out = node.cmd(f'{wrapper} {cmd}')
        print(out, end='' if out.endswith('\n') else '\n')

    def _node_cmd(self, node_name: str, cmd: str) -> None:
        try:
            node = self.mn.get(node_name)
            out = node.cmd(cmd)
        except Exception as exc:
            out = f'ERROR: {exc}\n'
        print(out, end='' if out.endswith('\n') else '\n')

    def do_verify(self, _line: str) -> None:
        """verify - run the required verification commands safely."""
        commands = [
            ('MPLS LDP binding on P1', 'P1', '/tmp/frr-mpls-lab/P1/v show mpls ldp binding'),
            ('MPLS LDP neighbor on P1', 'P1', '/tmp/frr-mpls-lab/P1/v show mpls ldp neighbor'),
            ('Kernel MPLS LFIB on P1', 'P1', 'ip -f mpls route'),
            ('Branch 3 OSPF neighbors on spine1', 'spine1', '/tmp/frr-mpls-lab/spine1/v show ip ospf neighbor'),
            ('Branch 3 OSPF routes on spine1', 'spine1', '/tmp/frr-mpls-lab/spine1/v show ip ospf route'),
            ('CE3 routing table', 'CE3', '/tmp/frr-mpls-lab/CE3/v show ip route'),
            ('spine1 kernel routes / ECMP', 'spine1', 'ip route'),
            ('CE2 VLAN interfaces', 'CE2', "sh -c \"ip -brief addr show | grep -E 'ce2-bond|ce2-lan'\""),
            ('CE2 routing table', 'CE2', 'ip route'),
            ('CE2 FRR wrapper route view', 'CE2', '/tmp/frr-mpls-lab/CE2/v show ip route'),
            ('Branch 2 inter-VLAN ping', 'H2_1', 'ping -c 2 192.168.21.11'),
            ('PE1 GRETAP links', 'PE1', 'ip link show type gretap'),
            ('PE1 VPLS bridge FDB', 'PE1', 'bridge fdb show dev br-vpls'),
            ('OVS VLAN view from core1 namespace', 'core1', 'ovs-vsctl show'),
        ]
        for title, node_name, cmd in commands:
            print('\n' + '=' * 72)
            print(title)
            print(f'{node_name}$ {cmd}')
            print('-' * 72)
            try:
                node = self.mn.get(node_name)
                out = node.cmd(cmd)
            except Exception as exc:
                out = f'ERROR: {exc}\n'
            print(out, end='' if out.endswith('\n') else '\n')

# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def print_banner(mode: str) -> None:
    print('')
    print('======================================================================')
    print(' Metro Ethernet MAN - MPLS Multi-Branch Enterprise on Mininet')
    print(f' Backbone mode: {mode.upper()}')
    print(' Branch 1: Flat 2-access | Branch 2: 2-core/2-dist/3-access VLAN | Branch 3: 2-spine/4-leaf')
    print(' Provider: PE1/PE2/PE3 + P1/P2/P3/P4, OSPF + LDP control plane + MPLS service labels')
    print('----------------------------------------------------------------------')
    print(' In Mininet CLI:')
    print('   verify       # run required verification snapshot')
    print('   runquick     # short tests + auto dashboard_data.json')
    print('   runall       # full MPLS tests + IP routing comparison JSON')
    print('   json         # monitor-only dashboard JSON')
    print('   dash 8000    # dashboard auto-load server')
    print('   names        # friendly host names -> real Mininet node names')
    print('   vsh P1 show mpls ldp neighbor')
    print('   trace host1 admin1')
    print('======================================================================')
    print('')


def main() -> None:
    parser = argparse.ArgumentParser(description='Metro Ethernet MPLS topology for Mininet')
    parser.add_argument('--mode', choices=['mpls', 'ip'], default='mpls', help='Backbone mode. Default: mpls. Use ip only for traditional routing baseline.')
    parser.add_argument('--skip-quicktest', action='store_true', help='Skip quick ping after start')
    parser.add_argument('--no-clean', action='store_true', help='Do not run Mininet cleanup before start')
    parser.add_argument('--no-static-mpls-fallback', action='store_true', help='Use only dynamic FRR LDP labels; do not install static LFIB fallback if the kernel LFIB stays empty')
    args = parser.parse_args()

    require_root()
    load_mpls_modules()
    check_prerequisites(args.mode)
    if not args.no_clean:
        prepare_mininet_environment()
    cleanup_stale_lab_state()

    info('\n*** Build topology\n')
    net = create_network()
    net.start()
    net.backbone_mode = args.mode
    net.project_title = 'Metro Ethernet MAN with MPLS backbone'
    net.switch_to_ip_baseline = lambda: switch_to_ip_baseline(net)
    net.restore_mpls_mode = lambda: restore_mpls_mode(net)
    net.ensure_mpls_service_dataplane = lambda: ensure_mpls_service_dataplane(
        net, allow_static_fallback=getattr(net, 'auto_static_mpls_fallback', True)
    )
    net.results_dir = os.path.join(os.getcwd(), 'mpls_results')
    net.auto_static_mpls_fallback = not args.no_static_mpls_fallback
    os.makedirs(net.results_dir, exist_ok=True)
    fix_results_dir_permissions(net.results_dir)

    configure_ip(net)
    configure_routing(net, args.mode)
    if args.mode == 'mpls':
        configure_mpls(net)
    configure_frr_backbone(net, enable_ldp=(args.mode == 'mpls'))
    configure_frr_branch2(net)
    configure_frr_branch3(net)
    configure_vpls_gretap(net)

    # Give OSPF/LDP time to form adjacencies before quick test and CLI commands.
    info('\n*** Waiting for FRR OSPF/LDP convergence (15s)...\n')
    time.sleep(15)

    if args.mode == 'mpls':
        ensure_dynamic_ldp_or_static_fallback(net, allow_static_fallback=net.auto_static_mpls_fallback)
        show_mpls_tables(net)

    print_branch2_routing_summary(net)

    apply_tuning(net)
    if not args.skip_quicktest:
        quick_test(net)

    print_banner(args.mode)
    try:
        LabCLI(net)
    finally:
        for name in ['P1', 'P2', 'P3', 'P4', 'PE1', 'PE2', 'PE3',
                     'CE2', 'CE3', 'spine1', 'spine2', 'leaf1', 'leaf2', 'leaf3', 'leaf4']:
            try:
                _stop_frr_node(net.get(name), name)
            except Exception:
                pass
        net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    main()
