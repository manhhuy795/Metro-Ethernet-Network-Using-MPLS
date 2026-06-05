from __future__ import annotations

from mininet.log import info, warn


BRANCH2_SWITCH_DPIDS = {
    'core1': '0000000000000011',
    'core2': '0000000000000012',
    'dist1': '0000000000000013',
    'dist2': '0000000000000014',
    'access1': '0000000000000015',
    'access2': '0000000000000016',
    'access3': '0000000000000017',
}
BRANCH2_SWITCHES = list(BRANCH2_SWITCH_DPIDS.keys())
BRANCH2_STALE_INTFS = [
    'ce2-lan1', 'ce2-lan2', 'ce2-bond', 'ce2-bond.10', 'ce2-bond.20', 'ce2-bond.30',
    'core1-ce2', 'core2-ce2', 'core1-core2', 'core2-core1',
    'core1-dist1', 'dist1-core1', 'core1-dist2', 'dist2-core1',
    'core2-dist1', 'dist1-core2', 'core2-dist2', 'dist2-core2',
    'dist1-dist2', 'dist2-dist1',
    'dist1-access1', 'access1-dist1', 'dist1-access2', 'access2-dist1', 'dist1-access3', 'access3-dist1',
    'dist2-access1', 'access1-dist2', 'dist2-access2', 'access2-dist2', 'dist2-access3', 'access3-dist2',
    'access1-h2_1', 'access1-h2_2', 'access2-h2_3', 'access2-h2_4',
    'access3-h2_5', 'access3-h2_6',
]

B2_TRUNK_VLANS = '10,20,30'
B2_ACCESS_PORTS = {
    'access1-h2_1': 10,
    'access1-h2_2': 10,
    'access2-h2_3': 20,
    'access2-h2_4': 20,
    'access3-h2_5': 30,
    'access3-h2_6': 30,
}
B2_TRUNK_PORTS = [
    'core1-ce2', 'core2-ce2', 'core1-core2', 'core2-core1',
    'core1-dist1', 'dist1-core1', 'core1-dist2', 'dist2-core1',
    'core2-dist1', 'dist1-core2', 'core2-dist2', 'dist2-core2',
    'dist1-dist2', 'dist2-dist1',
    'dist1-access1', 'access1-dist1', 'dist1-access2', 'access2-dist1', 'dist1-access3', 'access3-dist1',
    'dist2-access1', 'access1-dist2', 'dist2-access2', 'access2-dist2', 'dist2-access3', 'access3-dist2',
]


def _exec_many(node, commands) -> None:
    for cmd in commands:
        node.cmd(cmd)


def build_branch2(net, ce2, TCLink, LAN_BW: int, LAN_DL: str, add_named_switch) -> None:
    """Create Branch 2 three-layer LAN according to the diagrams."""
    core1 = add_named_switch(net, 'core1', BRANCH2_SWITCH_DPIDS['core1'])
    core2 = add_named_switch(net, 'core2', BRANCH2_SWITCH_DPIDS['core2'])
    dist1 = add_named_switch(net, 'dist1', BRANCH2_SWITCH_DPIDS['dist1'])
    dist2 = add_named_switch(net, 'dist2', BRANCH2_SWITCH_DPIDS['dist2'])
    access1 = add_named_switch(net, 'access1', BRANCH2_SWITCH_DPIDS['access1'])
    access2 = add_named_switch(net, 'access2', BRANCH2_SWITCH_DPIDS['access2'])
    access3 = add_named_switch(net, 'access3', BRANCH2_SWITCH_DPIDS['access3'])

    h2_1 = net.addHost('H2_1', ip='192.168.20.11/24', defaultRoute='via 192.168.20.1')  # admin1
    h2_2 = net.addHost('H2_2', ip='192.168.20.12/24', defaultRoute='via 192.168.20.1')  # admin2
    h2_3 = net.addHost('H2_3', ip='192.168.21.11/24', defaultRoute='via 192.168.21.1')  # lab1
    h2_4 = net.addHost('H2_4', ip='192.168.21.12/24', defaultRoute='via 192.168.21.1')  # lab2
    h2_5 = net.addHost('H2_5', ip='192.168.22.11/24', defaultRoute='via 192.168.22.1')  # guest1
    h2_6 = net.addHost('H2_6', ip='192.168.22.12/24', defaultRoute='via 192.168.22.1')  # guest2

    # CE2 to both core switches.
    net.addLink(ce2, core1, intfName1='ce2-lan1', intfName2='core1-ce2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(ce2, core2, intfName1='ce2-lan2', intfName2='core2-ce2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    # Core layer and core-to-distribution cross links.
    net.addLink(core1, core2, intfName1='core1-core2', intfName2='core2-core1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(core1, dist1, intfName1='core1-dist1', intfName2='dist1-core1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(core1, dist2, intfName1='core1-dist2', intfName2='dist2-core1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(core2, dist1, intfName1='core2-dist1', intfName2='dist1-core2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(core2, dist2, intfName1='core2-dist2', intfName2='dist2-core2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(dist1, dist2, intfName1='dist1-dist2', intfName2='dist2-dist1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    # Distribution to every access switch for the redundant 3-layer shape.
    for dist in [dist1, dist2]:
        for access in [access1, access2, access3]:
            net.addLink(dist, access, intfName1=f'{dist.name}-{access.name}', intfName2=f'{access.name}-{dist.name}', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    net.addLink(h2_1, access1, intfName2='access1-h2_1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h2_2, access1, intfName2='access1-h2_2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h2_3, access2, intfName2='access2-h2_3', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h2_4, access2, intfName2='access2-h2_4', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h2_5, access3, intfName2='access3-h2_5', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h2_6, access3, intfName2='access3-h2_6', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)


def configure_branch2_ovs(net) -> None:
    """Configure Core/Distribution/Access trunk and access VLANs."""
    info('  [Branch2] Configure OVS RSTP + trunk/access VLANs\n')
    for sw_name in BRANCH2_SWITCHES:
        sw = net.get(sw_name)
        # Redundant L2 links need RSTP.  Otherwise OVS standalone bridges would loop.
        sw.cmd(f'ovs-vsctl set bridge {sw_name} stp_enable=false rstp_enable=true')

    for port in B2_TRUNK_PORTS:
        sw_name = port.split('-')[0]
        net.get(sw_name).cmd(
            f'ovs-vsctl set port {port} vlan_mode=trunk trunks={B2_TRUNK_VLANS} -- clear port {port} tag 2>/dev/null || true'
        )

    for port, vlan in B2_ACCESS_PORTS.items():
        sw_name = port.split('-')[0]
        net.get(sw_name).cmd(
            f'ovs-vsctl set port {port} vlan_mode=access tag={vlan} 2>/dev/null || true'
        )


def configure_branch2_ip(net) -> None:
    """Configure CE2 router-on-a-stick VLAN gateways on an active-backup bond."""
    configure_branch2_ovs(net)
    ce2 = net.get('CE2')
    _exec_many(ce2, [
        'modprobe bonding >/dev/null 2>&1 || true',
        'ip link add ce2-bond type bond mode active-backup miimon 100 2>/dev/null || true',
        'ip link set ce2-lan1 down 2>/dev/null || true',
        'ip link set ce2-lan2 down 2>/dev/null || true',
        'ip addr flush dev ce2-lan1 2>/dev/null || true',
        'ip addr flush dev ce2-lan2 2>/dev/null || true',
        'ip link set ce2-lan1 master ce2-bond 2>/dev/null || true',
        'ip link set ce2-lan2 master ce2-bond 2>/dev/null || true',
        'ip link set ce2-lan1 up',
        'ip link set ce2-lan2 up',
        'ip link set ce2-bond up',
        'ip link add link ce2-bond name ce2-bond.10 type vlan id 10 2>/dev/null || true',
        'ip link add link ce2-bond name ce2-bond.20 type vlan id 20 2>/dev/null || true',
        'ip link add link ce2-bond name ce2-bond.30 type vlan id 30 2>/dev/null || true',
        'ip addr add 192.168.20.1/24 dev ce2-bond.10 2>/dev/null || true',
        'ip addr add 192.168.21.1/24 dev ce2-bond.20 2>/dev/null || true',
        'ip addr add 192.168.22.1/24 dev ce2-bond.30 2>/dev/null || true',
        'ip link set dev ce2-bond.10 up',
        'ip link set dev ce2-bond.20 up',
        'ip link set dev ce2-bond.30 up',
    ])


def start_branch2_frr(net, frr_available, start_frr_node) -> None:
    """Start zebra on CE2 so `CE2 v show ip route` reflects kernel routes."""
    if not frr_available(['zebra']):
        warn('FRRouting zebra is missing. Branch 2 vtysh route output will not be available.\n')
        return
    info('\n*** Start FRR zebra on Branch 2 CE2 router-on-a-stick gateway\n')
    start_frr_node(net.get('CE2'), 'CE2', ospf=None, ldp=None)


def print_branch2_routing_summary(net) -> None:
    ce2 = net.get('CE2')
    print('\n[Branch 2 routing summary]')
    print('CE2 is the inter-VLAN gateway for VLAN 10/20/30:')
    print(ce2.cmd("ip -brief addr show | grep -E 'ce2-bond|ce2-lan' || true"))
    print('CE2 routes Branch 2 prefixes and remote branches via PE2:')
    print(ce2.cmd(r'ip route show | grep -E "192\.168\.(10|20|21|22|30|31|32)\." || true'))
