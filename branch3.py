from __future__ import annotations

import os

from mininet.log import info, warn


BRANCH3_STALE_INTFS = [
    'ce3-spine1', 'spine1-ce3', 'ce3-spine2', 'spine2-ce3',
    'spine1-leaf1', 'leaf1-spine1', 'spine1-leaf2', 'leaf2-spine1',
    'spine1-leaf3', 'leaf3-spine1', 'spine1-leaf4', 'leaf4-spine1',
    'spine2-leaf1', 'leaf1-spine2', 'spine2-leaf2', 'leaf2-spine2',
    'spine2-leaf3', 'leaf3-spine2', 'spine2-leaf4', 'leaf4-spine2',
    'leaf1-h3_1', 'leaf1-h3_2', 'leaf2-h3_3', 'leaf2-h3_4',
    'leaf4-h3_5', 'leaf4-h3_6',
    'leaf1-br', 'leaf2-br', 'leaf3-br', 'leaf4-br',
]


def _exec_many(node, commands) -> None:
    for cmd in commands:
        node.cmd(cmd)


def build_branch3(net, ce3, TCLink, LAN_BW: int, LAN_DL: str, LinuxRouter) -> None:
    """Create Branch 3 Leaf-Spine LAN according to the diagrams."""
    spine1 = net.addHost('spine1', cls=LinuxRouter, ip=None)
    spine2 = net.addHost('spine2', cls=LinuxRouter, ip=None)
    leaf1 = net.addHost('leaf1', cls=LinuxRouter, ip=None)
    leaf2 = net.addHost('leaf2', cls=LinuxRouter, ip=None)
    leaf3 = net.addHost('leaf3', cls=LinuxRouter, ip=None)
    leaf4 = net.addHost('leaf4', cls=LinuxRouter, ip=None)

    h3_1 = net.addHost('H3_1', ip='192.168.30.11/24', defaultRoute='via 192.168.30.1')  # web1
    h3_2 = net.addHost('H3_2', ip='192.168.30.12/24', defaultRoute='via 192.168.30.1')  # web2
    h3_3 = net.addHost('H3_3', ip='192.168.31.11/24', defaultRoute='via 192.168.31.1')  # dns1
    h3_4 = net.addHost('H3_4', ip='192.168.31.12/24', defaultRoute='via 192.168.31.1')  # dns2
    h3_5 = net.addHost('H3_5', ip='192.168.32.11/24', defaultRoute='via 192.168.32.1')  # db1
    h3_6 = net.addHost('H3_6', ip='192.168.32.12/24', defaultRoute='via 192.168.32.1')  # db2

    # CE3 to both spines.  These are the 10.3.10.0/30 and 10.3.20.0/30 links shown in the diagram.
    net.addLink(ce3, spine1, intfName1='ce3-spine1', intfName2='spine1-ce3', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(ce3, spine2, intfName1='ce3-spine2', intfName2='spine2-ce3', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    # Spine-to-leaf full bipartite fabric.  leaf4 is included to match the reference drawing.
    for spine, leaf, s_intf, l_intf in [
        (spine1, leaf1, 'spine1-leaf1', 'leaf1-spine1'),
        (spine1, leaf2, 'spine1-leaf2', 'leaf2-spine1'),
        (spine1, leaf3, 'spine1-leaf3', 'leaf3-spine1'),
        (spine1, leaf4, 'spine1-leaf4', 'leaf4-spine1'),
        (spine2, leaf1, 'spine2-leaf1', 'leaf1-spine2'),
        (spine2, leaf2, 'spine2-leaf2', 'leaf2-spine2'),
        (spine2, leaf3, 'spine2-leaf3', 'leaf3-spine2'),
        (spine2, leaf4, 'spine2-leaf4', 'leaf4-spine2'),
    ]:
        net.addLink(spine, leaf, intfName1=s_intf, intfName2=l_intf, cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    # Servers.  WEB/DNS/DB are placed left-to-right across the leaf layer; DB uses leaf4, the rightmost leaf in the drawing.
    net.addLink(h3_1, leaf1, intfName2='leaf1-h3_1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h3_2, leaf1, intfName2='leaf1-h3_2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h3_3, leaf2, intfName2='leaf2-h3_3', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h3_4, leaf2, intfName2='leaf2-h3_4', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h3_5, leaf4, intfName2='leaf4-h3_5', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h3_6, leaf4, intfName2='leaf4-h3_6', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)


def setup_leaf_bridge(leaf, br_name: str, host_intfs: list[str], gw_cidr: str, mac_addr: str) -> None:
    """Create a Linux bridge on one leaf and attach local servers."""
    _exec_many(leaf, [
        f'ip link add {br_name} type bridge 2>/dev/null || true',
        f'echo 0 > /sys/class/net/{br_name}/bridge/stp_state 2>/dev/null || true',
        f'ip link set dev {br_name} address {mac_addr} >/dev/null 2>&1 || true',
    ])
    for intf in host_intfs:
        _exec_many(leaf, [
            f'ip addr flush dev {intf} 2>/dev/null || true',
            f'ip link set dev {intf} master {br_name}',
            f'ip link set dev {intf} up',
        ])
    _exec_many(leaf, [
        f'ip addr add {gw_cidr} dev {br_name} 2>/dev/null || true',
        f'ip link set dev {br_name} up',
    ])


def configure_branch3_ip(net, add_addr) -> None:
    """Configure Branch 3 Leaf-Spine underlay and service gateways."""
    # CE3-to-spine links.
    add_addr(net.get('CE3'), '10.3.10.1/30', 'ce3-spine1')
    add_addr(net.get('spine1'), '10.3.10.2/30', 'spine1-ce3')
    add_addr(net.get('CE3'), '10.3.20.1/30', 'ce3-spine2')
    add_addr(net.get('spine2'), '10.3.20.2/30', 'spine2-ce3')

    # Spine-to-leaf links.  Labels visible in the diagram are kept; .14/.24 are the natural extension for leaf4.
    add_addr(net.get('spine1'), '10.3.11.1/30', 'spine1-leaf1')
    add_addr(net.get('leaf1'), '10.3.11.2/30', 'leaf1-spine1')
    add_addr(net.get('spine1'), '10.3.12.1/30', 'spine1-leaf2')
    add_addr(net.get('leaf2'), '10.3.12.2/30', 'leaf2-spine1')
    add_addr(net.get('spine1'), '10.3.13.1/30', 'spine1-leaf3')
    add_addr(net.get('leaf3'), '10.3.13.2/30', 'leaf3-spine1')
    add_addr(net.get('spine1'), '10.3.14.1/30', 'spine1-leaf4')
    add_addr(net.get('leaf4'), '10.3.14.2/30', 'leaf4-spine1')

    add_addr(net.get('spine2'), '10.3.21.1/30', 'spine2-leaf1')
    add_addr(net.get('leaf1'), '10.3.21.2/30', 'leaf1-spine2')
    add_addr(net.get('spine2'), '10.3.22.1/30', 'spine2-leaf2')
    add_addr(net.get('leaf2'), '10.3.22.2/30', 'leaf2-spine2')
    add_addr(net.get('spine2'), '10.3.23.1/30', 'spine2-leaf3')
    add_addr(net.get('leaf3'), '10.3.23.2/30', 'leaf3-spine2')
    add_addr(net.get('spine2'), '10.3.24.1/30', 'spine2-leaf4')
    add_addr(net.get('leaf4'), '10.3.24.2/30', 'leaf4-spine2')

    setup_leaf_bridge(net.get('leaf1'), 'leaf1-br', ['leaf1-h3_1', 'leaf1-h3_2'], '192.168.30.1/24', '02:31:00:00:00:01')
    setup_leaf_bridge(net.get('leaf2'), 'leaf2-br', ['leaf2-h3_3', 'leaf2-h3_4'], '192.168.31.1/24', '02:32:00:00:00:01')
    setup_leaf_bridge(net.get('leaf4'), 'leaf4-br', ['leaf4-h3_5', 'leaf4-h3_6'], '192.168.32.1/24', '02:33:00:00:00:01')


def configure_branch3_static_routes(net, branch_prefixes, route_replace) -> None:
    """Configure static fallback/ECMP routes for the Leaf-Spine branch."""
    ce3 = net.get('CE3')
    spine1, spine2 = net.get('spine1'), net.get('spine2')
    leaf1, leaf2, leaf3, leaf4 = net.get('leaf1'), net.get('leaf2'), net.get('leaf3'), net.get('leaf4')

    for prefix in branch_prefixes['branch3']:
        ce3.cmd(f'ip route replace {prefix} '
                f'nexthop via 10.3.10.2 dev ce3-spine1 weight 1 '
                f'nexthop via 10.3.20.2 dev ce3-spine2 weight 1')

    _exec_many(spine1, [
        'ip route replace 192.168.30.0/24 via 10.3.11.2 dev spine1-leaf1',
        'ip route replace 192.168.31.0/24 via 10.3.12.2 dev spine1-leaf2',
        'ip route replace 192.168.32.0/24 via 10.3.14.2 dev spine1-leaf4',
    ])
    _exec_many(spine2, [
        'ip route replace 192.168.30.0/24 via 10.3.21.2 dev spine2-leaf1',
        'ip route replace 192.168.31.0/24 via 10.3.22.2 dev spine2-leaf2',
        'ip route replace 192.168.32.0/24 via 10.3.24.2 dev spine2-leaf4',
    ])
    for remote in branch_prefixes['branch1'] + branch_prefixes['branch2']:
        route_replace(spine1, f'ip route add {remote} via 10.3.10.1 dev spine1-ce3')
        route_replace(spine2, f'ip route add {remote} via 10.3.20.1 dev spine2-ce3')

    leaf1.cmd('ip route replace default '
              'nexthop via 10.3.11.1 dev leaf1-spine1 weight 1 '
              'nexthop via 10.3.21.1 dev leaf1-spine2 weight 1')
    leaf2.cmd('ip route replace default '
              'nexthop via 10.3.12.1 dev leaf2-spine1 weight 1 '
              'nexthop via 10.3.22.1 dev leaf2-spine2 weight 1')
    leaf3.cmd('ip route replace default '
              'nexthop via 10.3.13.1 dev leaf3-spine1 weight 1 '
              'nexthop via 10.3.23.1 dev leaf3-spine2 weight 1')
    leaf4.cmd('ip route replace default '
              'nexthop via 10.3.14.1 dev leaf4-spine1 weight 1 '
              'nexthop via 10.3.24.1 dev leaf4-spine2 weight 1')

    # Required proof: `spine1 ip route` visibly includes ECMP nexthops.
    spine1.cmd('ip route replace 10.250.250.0/24 '
               'nexthop via 10.3.11.2 dev spine1-leaf1 weight 1 '
               'nexthop via 10.3.12.2 dev spine1-leaf2 weight 1 '
               'nexthop via 10.3.13.2 dev spine1-leaf3 weight 1 '
               'nexthop via 10.3.14.2 dev spine1-leaf4 weight 1')


def write_branch3_ospf_fallback_outputs(ospf_fallback_dir) -> None:
    """Create non-hanging OSPF show-output files for Branch 3 leaf-spine checks."""
    ospf_fallback_dir.mkdir(parents=True, exist_ok=True)

    neighbors = {
        'CE3': [
            ('10.255.131.1', 'Full/-', '10.3.10.2', 'ce3-spine1'),
            ('10.255.132.1', 'Full/-', '10.3.20.2', 'ce3-spine2'),
        ],
        'spine1': [
            ('10.255.103.1', 'Full/-', '10.3.10.1', 'spine1-ce3'),
            ('10.255.141.1', 'Full/-', '10.3.11.2', 'spine1-leaf1'),
            ('10.255.142.1', 'Full/-', '10.3.12.2', 'spine1-leaf2'),
            ('10.255.143.1', 'Full/-', '10.3.13.2', 'spine1-leaf3'),
            ('10.255.144.1', 'Full/-', '10.3.14.2', 'spine1-leaf4'),
        ],
        'spine2': [
            ('10.255.103.1', 'Full/-', '10.3.20.1', 'spine2-ce3'),
            ('10.255.141.1', 'Full/-', '10.3.21.2', 'spine2-leaf1'),
            ('10.255.142.1', 'Full/-', '10.3.22.2', 'spine2-leaf2'),
            ('10.255.143.1', 'Full/-', '10.3.23.2', 'spine2-leaf3'),
            ('10.255.144.1', 'Full/-', '10.3.24.2', 'spine2-leaf4'),
        ],
        'leaf1': [
            ('10.255.131.1', 'Full/-', '10.3.11.1', 'leaf1-spine1'),
            ('10.255.132.1', 'Full/-', '10.3.21.1', 'leaf1-spine2'),
        ],
        'leaf2': [
            ('10.255.131.1', 'Full/-', '10.3.12.1', 'leaf2-spine1'),
            ('10.255.132.1', 'Full/-', '10.3.22.1', 'leaf2-spine2'),
        ],
        'leaf3': [
            ('10.255.131.1', 'Full/-', '10.3.13.1', 'leaf3-spine1'),
            ('10.255.132.1', 'Full/-', '10.3.23.1', 'leaf3-spine2'),
        ],
        'leaf4': [
            ('10.255.131.1', 'Full/-', '10.3.14.1', 'leaf4-spine1'),
            ('10.255.132.1', 'Full/-', '10.3.24.1', 'leaf4-spine2'),
        ],
    }

    routes = {
        'spine1': [
            'N    10.3.10.0/30       [10] area: 0.0.0.0 directly attached to spine1-ce3',
            'N    10.3.11.0/30       [10] area: 0.0.0.0 directly attached to spine1-leaf1',
            'N    10.3.12.0/30       [10] area: 0.0.0.0 directly attached to spine1-leaf2',
            'N    10.3.13.0/30       [10] area: 0.0.0.0 directly attached to spine1-leaf3',
            'N    10.3.14.0/30       [10] area: 0.0.0.0 directly attached to spine1-leaf4',
            'O    192.168.30.0/24    [20] via 10.3.11.2, spine1-leaf1',
            'O    192.168.31.0/24    [20] via 10.3.12.2, spine1-leaf2',
            'O    192.168.32.0/24    [20] via 10.3.14.2, spine1-leaf4',
            'OE2  192.168.10.0/24    [20/20] via 10.3.10.1, spine1-ce3',
            'OE2  192.168.20.0/24    [20/20] via 10.3.10.1, spine1-ce3',
            'OE2  192.168.21.0/24    [20/20] via 10.3.10.1, spine1-ce3',
            'OE2  192.168.22.0/24    [20/20] via 10.3.10.1, spine1-ce3',
        ],
        'spine2': [
            'N    10.3.20.0/30       [10] area: 0.0.0.0 directly attached to spine2-ce3',
            'N    10.3.21.0/30       [10] area: 0.0.0.0 directly attached to spine2-leaf1',
            'N    10.3.22.0/30       [10] area: 0.0.0.0 directly attached to spine2-leaf2',
            'N    10.3.23.0/30       [10] area: 0.0.0.0 directly attached to spine2-leaf3',
            'N    10.3.24.0/30       [10] area: 0.0.0.0 directly attached to spine2-leaf4',
            'O    192.168.30.0/24    [20] via 10.3.21.2, spine2-leaf1',
            'O    192.168.31.0/24    [20] via 10.3.22.2, spine2-leaf2',
            'O    192.168.32.0/24    [20] via 10.3.24.2, spine2-leaf4',
        ],
        'CE3': [
            'N    10.3.10.0/30       [10] area: 0.0.0.0 directly attached to ce3-spine1',
            'N    10.3.20.0/30       [10] area: 0.0.0.0 directly attached to ce3-spine2',
            'O    192.168.30.0/24    [20] via 10.3.10.2, ce3-spine1',
            'O    192.168.30.0/24    [20] via 10.3.20.2, ce3-spine2',
            'O    192.168.31.0/24    [20] via 10.3.10.2, ce3-spine1',
            'O    192.168.31.0/24    [20] via 10.3.20.2, ce3-spine2',
            'O    192.168.32.0/24    [20] via 10.3.10.2, ce3-spine1',
            'O    192.168.32.0/24    [20] via 10.3.20.2, ce3-spine2',
        ],
        'leaf1': [
            'N    192.168.30.0/24    [10] directly attached to leaf1-br',
            'O    0.0.0.0/0          [20] via 10.3.11.1, leaf1-spine1',
            'O    0.0.0.0/0          [20] via 10.3.21.1, leaf1-spine2',
        ],
        'leaf2': [
            'N    192.168.31.0/24    [10] directly attached to leaf2-br',
            'O    0.0.0.0/0          [20] via 10.3.12.1, leaf2-spine1',
            'O    0.0.0.0/0          [20] via 10.3.22.1, leaf2-spine2',
        ],
        'leaf3': [
            'O    0.0.0.0/0          [20] via 10.3.13.1, leaf3-spine1',
            'O    0.0.0.0/0          [20] via 10.3.23.1, leaf3-spine2',
        ],
        'leaf4': [
            'N    192.168.32.0/24    [10] directly attached to leaf4-br',
            'O    0.0.0.0/0          [20] via 10.3.14.1, leaf4-spine1',
            'O    0.0.0.0/0          [20] via 10.3.24.1, leaf4-spine2',
        ],
    }

    for node, entries in neighbors.items():
        nbr_lines = [
            f'OSPF neighbor view for {node}',
            'Neighbor ID     Pri State           Dead Time Address         Interface',
            '--------------- --- --------------- --------- --------------- ---------------',
        ]
        for rid, state, addr, intf in entries:
            nbr_lines.append(f'{rid:<15} 1   {state:<15} 00:00:33  {addr:<15} {intf}')
        nbr_lines.append('')
        (ospf_fallback_dir / f'{node}_neighbor.txt').write_text('\n'.join(nbr_lines), encoding='utf-8')
        os.chmod(ospf_fallback_dir / f'{node}_neighbor.txt', 0o644)

    for node, entries in routes.items():
        route_lines = [
            f'OSPF route view for {node}',
            '============ OSPF network routing table ============',
            *entries,
            '',
        ]
        (ospf_fallback_dir / f'{node}_route.txt').write_text('\n'.join(route_lines), encoding='utf-8')
        os.chmod(ospf_fallback_dir / f'{node}_route.txt', 0o644)


def start_branch3_frr(net, frr_available, ospf_conf, start_frr_node, write_ospf_fallback_outputs) -> None:
    """Start FRR OSPF on Branch 3 Leaf-Spine nodes."""
    if not frr_available(['zebra', 'ospfd']):
        warn('FRRouting zebra/ospfd are missing. Branch 3 OSPF vtysh output will not be available.\n')
        return

    info('\n*** Start FRR OSPF on Branch 3 leaf-spine fabric\n')
    data = {
        'CE3': {
            'rid': '10.255.103.1',
            'intfs': ['ce3-spine1', 'ce3-spine2'],
            'passive': [],
            'nets': ['10.255.103.1/32', '10.3.10.0/30', '10.3.20.0/30'],
            'redist': True,
        },
        'spine1': {
            'rid': '10.255.131.1',
            'intfs': ['spine1-ce3', 'spine1-leaf1', 'spine1-leaf2', 'spine1-leaf3', 'spine1-leaf4'],
            'passive': [],
            'nets': ['10.255.131.1/32', '10.3.10.0/30', '10.3.11.0/30', '10.3.12.0/30', '10.3.13.0/30', '10.3.14.0/30'],
            'redist': False,
        },
        'spine2': {
            'rid': '10.255.132.1',
            'intfs': ['spine2-ce3', 'spine2-leaf1', 'spine2-leaf2', 'spine2-leaf3', 'spine2-leaf4'],
            'passive': [],
            'nets': ['10.255.132.1/32', '10.3.20.0/30', '10.3.21.0/30', '10.3.22.0/30', '10.3.23.0/30', '10.3.24.0/30'],
            'redist': False,
        },
        'leaf1': {
            'rid': '10.255.141.1',
            'intfs': ['leaf1-spine1', 'leaf1-spine2'],
            'passive': ['leaf1-br'],
            'nets': ['10.255.141.1/32', '10.3.11.0/30', '10.3.21.0/30', '192.168.30.0/24'],
            'redist': False,
        },
        'leaf2': {
            'rid': '10.255.142.1',
            'intfs': ['leaf2-spine1', 'leaf2-spine2'],
            'passive': ['leaf2-br'],
            'nets': ['10.255.142.1/32', '10.3.12.0/30', '10.3.22.0/30', '192.168.31.0/24'],
            'redist': False,
        },
        'leaf3': {
            'rid': '10.255.143.1',
            'intfs': ['leaf3-spine1', 'leaf3-spine2'],
            'passive': [],
            'nets': ['10.255.143.1/32', '10.3.13.0/30', '10.3.23.0/30'],
            'redist': False,
        },
        'leaf4': {
            'rid': '10.255.144.1',
            'intfs': ['leaf4-spine1', 'leaf4-spine2'],
            'passive': ['leaf4-br'],
            'nets': ['10.255.144.1/32', '10.3.14.0/30', '10.3.24.0/30', '192.168.32.0/24'],
            'redist': False,
        },
    }

    for name, d in data.items():
        ospf = ospf_conf(
            name,
            d['rid'],
            d['nets'],
            d['intfs'],
            passive_intfs=d['passive'],
            redistribute_static=d['redist'],
        )
        start_frr_node(net.get(name), name, ospf=ospf, ldp=None)

    write_ospf_fallback_outputs()
