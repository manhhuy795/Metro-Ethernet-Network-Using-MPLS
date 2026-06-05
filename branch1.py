from __future__ import annotations

from mininet.log import info


BRANCH1_SWITCH_DPIDS = {
    'flat1': '0000000000000001',
    'flat2': '0000000000000002',
}
BRANCH1_SWITCHES = list(BRANCH1_SWITCH_DPIDS.keys())
BRANCH1_STALE_INTFS = [
    'ce1-lan1', 'ce1-lan2', 'br-ce1-flat',
    'flat1-ce1', 'flat2-ce1',
    'flat1-h1_1', 'flat1-h1_2',
    'flat2-h1_3', 'flat2-h1_4',
]


def build_branch1(net, ce1, TCLink, LAN_BW: int, LAN_DL: str, add_named_switch) -> None:
    """Create Branch 1 flat LAN with two access switches."""
    flat1 = add_named_switch(net, 'flat1', BRANCH1_SWITCH_DPIDS['flat1'])
    flat2 = add_named_switch(net, 'flat2', BRANCH1_SWITCH_DPIDS['flat2'])

    h1_1 = net.addHost('H1_1', ip='192.168.10.11/24', defaultRoute='via 192.168.10.1')
    h1_2 = net.addHost('H1_2', ip='192.168.10.12/24', defaultRoute='via 192.168.10.1')
    h1_3 = net.addHost('H1_3', ip='192.168.10.13/24', defaultRoute='via 192.168.10.1')
    h1_4 = net.addHost('H1_4', ip='192.168.10.14/24', defaultRoute='via 192.168.10.1')

    # CE1 has two LAN-facing ports, one to each access switch.  They are
    # bridged in configure_branch1_ip(), giving one flat subnet.
    net.addLink(ce1, flat1, intfName1='ce1-lan1', intfName2='flat1-ce1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(ce1, flat2, intfName1='ce1-lan2', intfName2='flat2-ce1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)

    net.addLink(h1_1, flat1, intfName2='flat1-h1_1', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h1_2, flat1, intfName2='flat1-h1_2', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h1_3, flat2, intfName2='flat2-h1_3', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)
    net.addLink(h1_4, flat2, intfName2='flat2-h1_4', cls=TCLink, bw=LAN_BW, delay=LAN_DL, loss=0, use_hfsc=True)


def configure_branch1_ip(net, add_addr) -> None:
    """Configure CE1 as a single flat-LAN gateway across two access switches."""
    info('  [Branch1] Configure flat LAN gateway 192.168.10.1/24 on CE1 bridge\n')
    ce1 = net.get('CE1')
    ce1.cmd('ip link add br-ce1-flat type bridge stp_state 0 forward_delay 0 2>/dev/null || true')
    for intf in ['ce1-lan1', 'ce1-lan2']:
        ce1.cmd(f'ip addr flush dev {intf} 2>/dev/null || true')
        ce1.cmd(f'ip link set dev {intf} master br-ce1-flat 2>/dev/null || true')
        ce1.cmd(f'ip link set dev {intf} up')
    ce1.cmd('ip link set dev br-ce1-flat up')
    add_addr(ce1, '192.168.10.1/24', 'br-ce1-flat')
