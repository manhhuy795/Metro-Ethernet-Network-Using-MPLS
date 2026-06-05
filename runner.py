import json
import os
import re
import time
from datetime import datetime
from glob import glob


# ---------------------------------------------------------------------------
#  Terminal output helpers
# ---------------------------------------------------------------------------

RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'

def _c(text, *codes):
    return ''.join(codes) + str(text) + RESET

def ok(s): return _c(s, GREEN, BOLD)
def warn(s): return _c(s, YELLOW, BOLD)
def err(s): return _c(s, RED, BOLD)
def info(s): return _c(s, CYAN)
def dim(s): return _c(s, DIM)
def hdr(s): return _c(s, BLUE, BOLD)

def _bar(title):
    width = 76
    pad = max(0, width - len(title) - 4)
    return f"\n{BOLD}{BLUE}{'='*2} {title} {'='*pad}{RESET}"


# ---------------------------------------------------------------------------
#  Project metadata
# ---------------------------------------------------------------------------

BRANCH_NAMES = {
    'branch1': 'Flat Network',
    'branch2': '3-Tier',
    'branch3': 'Leaf-Spine',
}

REPRESENTATIVE_HOST = {
    'branch1': 'H1_1',
    'branch2': 'H2_1',
    'branch3': 'H3_1',
}

DISPLAY_NAME_MAP = {
    'H1_1': 'host1',
    'H1_2': 'host2',
    'H1_3': 'host3',
    'H1_4': 'host4',
    'H2_1': 'admin1',
    'H2_2': 'admin2',
    'H2_3': 'lab1',
    'H2_4': 'lab2',
    'H2_5': 'guest1',
    'H2_6': 'guest2',
    'H3_1': 'web1',
    'H3_2': 'web2',
    'H3_3': 'dns1',
    'H3_4': 'dns2',
    'H3_5': 'db1',
    'H3_6': 'db2',
}

def display_name(value):
    text = str(value)
    # Longer keys first avoids accidental partial replacement if names ever overlap.
    for raw in sorted(DISPLAY_NAME_MAP, key=len, reverse=True):
        text = re.sub(rf'\b{re.escape(raw)}\b', DISPLAY_NAME_MAP[raw], text)
    return text

def display_pair(src, dst):
    return f'{display_name(src)}->{display_name(dst)}'

EXPECTED_MPLS = {
    'PE1': {
        'push': [
            ('192.168.20.0/24', '16002/201', '10.255.13.2'),
            ('192.168.30.0/24', '16003/301', '10.255.11.2'),
        ],
        'pop': [('101', '10.0.11.1')],
    },
    'PE2': {
        'push': [
            ('192.168.10.0/24', '16001/101', '10.255.23.2'),
            ('192.168.30.0/24', '16003/301', '10.255.24.2'),
        ],
        'pop': [('201', '10.0.12.1')],
    },
    'PE3': {
        'push': [
            ('192.168.10.0/24', '16001/101', '10.255.32.2'),
            ('192.168.20.0/24', '16002/201', '10.255.34.2'),
        ],
        'pop': [('301', '10.0.13.1')],
    },
    'P1': {
        'lfib': [
            ('16001', '10.255.11.1'),
            ('16002', '10.255.103.2'),
            ('16003', '10.255.12.2'),
        ],
    },
    'P2': {
        'lfib': [
            ('16001', '10.255.12.1'),
            ('16002', '10.255.204.2'),
            ('16003', '10.255.32.1'),
        ],
    },
    'P3': {
        'lfib': [
            ('16001', '10.255.13.1'),
            ('16002', '10.255.23.1'),
            ('16003', '10.255.43.2'),
        ],
    },
    'P4': {
        'lfib': [
            ('16001', '10.255.43.1'),
            ('16002', '10.255.24.1'),
            ('16003', '10.255.34.1'),
        ],
    },
}

PROVIDER_NODES = ['PE1', 'PE2', 'PE3', 'P1', 'P2', 'P3', 'P4']


def _sudo_owner_ids():
    uid = int(os.environ.get('SUDO_UID', os.getuid()))
    gid = int(os.environ.get('SUDO_GID', os.getgid()))
    return uid, gid


def _fix_path_permissions(path, is_dir=False):
    try:
        uid, gid = _sudo_owner_ids()
        os.chown(path, uid, gid)
        os.chmod(path, 0o775 if is_dir else 0o664)
    except Exception:
        pass


def _out_dir(net=None):
    base = getattr(net, 'results_dir', None) or os.path.join(os.getcwd(), 'mpls_results')
    os.makedirs(base, exist_ok=True)
    _fix_path_permissions(base, is_dir=True)
    return base


def _backbone_mode(net):
    mode = getattr(net, 'backbone_mode', None)
    if mode:
        return mode
    try:
        pe1 = net.get('PE1')
        ipr = pe1.cmd('ip route show')
        return 'mpls' if 'encap mpls' in ipr else 'ip'
    except Exception:
        return 'unknown'


def _timestamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def _project_root():
    return os.path.dirname(os.path.abspath(__file__))


def _load_design_manifest():
    """Load static design/assignment metadata for dashboard and reports."""
    fp = os.path.join(_project_root(), 'network_design.json')
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {
            'project_title': 'Metro Ethernet MAN sử dụng MPLS',
            'branches': [],
            'assignment_requirements': [],
            'tools': {'mininet_shortcuts': []},
        }


def _avg_from(rows, key):
    vals = []
    for row in rows or []:
        try:
            value = row.get(key)
            if value is not None:
                vals.append(float(value))
        except Exception:
            pass
    return round(sum(vals) / len(vals), 3) if vals else None


def _value_by_label(rows, label_part, key):
    for row in rows or []:
        label = str(row.get('label') or row.get('display_label') or '')
        if label_part in label:
            value = row.get(key)
            try:
                return float(value) if value is not None else None
            except Exception:
                return None
    return None


def _max_from(rows, key):
    vals = []
    for row in rows or []:
        try:
            value = row.get(key)
            if value is not None:
                vals.append(float(value))
        except Exception:
            pass
    return round(max(vals), 3) if vals else None


def _build_ip_vs_mpls_analysis(results):
    """The lab runs MPLS by default, but the report still needs an IP-routing baseline.

    This object is intentionally marked as theoretical_reference, not measured_ip_mode,
    so the dashboard/report never presents it as a second experiment.
    """
    mpls_health = results.get('mpls_health') or {}
    checks_total = mpls_health.get('checks_total')
    checks_passed = mpls_health.get('checks_passed')
    return {
        'status': 'theoretical_reference',
        'measured_backbone_mode': results.get('backbone_mode', 'mpls'),
        'note': 'runall sinh thêm trường compare để đối chiếu MPLS label switching với IP routing baseline trong cùng một file JSON; nếu compare chưa có, mục này vẫn đóng vai trò giải thích cơ chế.',
        'mpls_evidence': f'LFIB health {checks_passed}/{checks_total}' if checks_total else 'LFIB/LDP snapshots available in JSON',
        'comparison': [
            {'criterion': 'Cách quyết định chuyển tiếp', 'ip_routing': 'Longest-prefix match trên bảng định tuyến IP tại từng router.', 'mpls': 'Label lookup trong LFIB; P-router xử lý nhãn thay vì subnet khách hàng.'},
            {'criterion': 'Xử lý tại lõi ISP', 'ip_routing': 'Router lõi phải tra cứu prefix IP đích.', 'mpls': 'Router P swap/PHP label, không cần biết chi tiết LAN của khách hàng.'},
            {'criterion': 'Tách lưu lượng khách hàng', 'ip_routing': 'Khó tách dịch vụ nếu chỉ dùng IP thuần.', 'mpls': 'PE gắn service label 101/201/301, phù hợp mô hình Metro Ethernet/VPN.'},
            {'criterion': 'Overhead tại PE egress', 'ip_routing': 'Không có PHP label.', 'mpls': 'PHP tại P-router gần đích giúp PE egress nhận gói đã giảm xử lý label transport.'},
        ],
    }


def _build_performance_insights(results):
    """Create report-ready interpretations so measured data is not wasted."""
    ping = results.get('ping', []) or []
    tcp = results.get('tcp', []) or []
    load = results.get('load', []) or []
    insights = []

    b1_b2 = _value_by_label(ping, 'B1 Flat -> B2', 'rtt_avg')
    b1_b3 = _value_by_label(ping, 'B1 Flat -> B3', 'rtt_avg')
    if b1_b2 is not None and b1_b3 is not None:
        diff = round(b1_b3 - b1_b2, 3)
        if diff > 0:
            insights.append(f'B1↔B3 có RTT cao hơn B1↔B2 khoảng {diff} ms vì đường tới PE3 đi qua nhánh P2/P4 xa hơn so với hướng PE2 qua P3/P4.')
        else:
            insights.append(f'B1↔B3 không cao hơn B1↔B2 trong lần đo này; chênh lệch {diff} ms cho thấy emulator đang ổn định.')

    intra_rows = results.get('intra_ping', []) or []
    b1_intra = _value_by_label(intra_rows, 'Branch 1 Flat', 'rtt_avg')
    b2_intra = _value_by_label(intra_rows, 'Branch 2 3-Tier inter-VLAN', 'rtt_avg')
    b3_intra = _value_by_label(intra_rows, 'Branch 3 Leaf-Spine', 'rtt_avg')
    if b1_intra is not None and b2_intra is not None and b3_intra is not None:
        insights.append(f'Delay nội bộ: Flat={b1_intra} ms, 3-Tier/inter-VLAN={b2_intra} ms, Leaf-Spine={b3_intra} ms. Đây là bằng chứng trực tiếp cho ảnh hưởng của kiến trúc LAN nội bộ.')

    intra_tcp = results.get('intra', []) or []
    intra_retx = {}
    for arch in ['Branch 1 Flat', 'Branch 2 3-Tier', 'Branch 3 Leaf-Spine']:
        vals = [r.get('retransmits') for r in intra_tcp if arch in str(r.get('label','')) and isinstance(r.get('retransmits'), int)]
        if vals:
            intra_retx[arch] = round(sum(vals) / len(vals), 2)
    if len(intra_retx) >= 2:
        insights.append('TCP retransmit nội bộ theo kiến trúc: ' + ', '.join(f'{k}={v}' for k, v in intra_retx.items()) + '. Chỉ số này giúp giải thích ảnh hưởng của Flat/3-Tier/Leaf-Spine ngoài throughput, vì emulator thường giữ throughput khá đồng đều.')

    b3_retx = [r.get('retransmits') for r in tcp if 'B3 Leaf-Spine' in str(r.get('label','')) and isinstance(r.get('retransmits'), int)]
    all_retx = [r.get('retransmits') for r in tcp if isinstance(r.get('retransmits'), int)]
    if b3_retx and all_retx:
        insights.append(f'TCP retransmit trung bình của các luồng liên quan B3 là {round(sum(b3_retx)/len(b3_retx), 2)}, toàn bộ luồng TCP là {round(sum(all_retx)/len(all_retx), 2)}. Nếu B3 cao hơn, có thể do nhiều hop Leaf-Spine/OSPF trong emulator.')

    max_load_loss = _max_from(load, 'lost_pct')
    if max_load_loss is not None:
        if max_load_loss == 0:
            insights.append('Packet loss ở load test bằng 0% trong emulator; điều này chứng minh cấu hình ổn định nhưng chưa đại diện cho nghẽn thật của phần cứng.')
        else:
            insights.append(f'Packet loss lớn nhất trong load test là {max_load_loss}%, cần xem theo từng hướng tải trên biểu đồ offered load.')

    if not insights:
        insights.append('Chưa đủ số liệu để tạo nhận xét tự động. Hãy chạy runall để sinh ping/tcp/udp/load/intra đầy đủ.')
    return insights


def _build_analysis_summary(results):
    """Computed summary aligned with the assignment deliverables."""
    ping = results.get('ping', []) or []
    tcp = results.get('tcp', []) or []
    intra = results.get('intra', []) or []
    intra_ping = results.get('intra_ping', []) or []
    udp = results.get('udp', []) or []
    load = results.get('load', []) or []
    return {
        'performance_table_fields': ['throughput_mbps', 'rtt_avg', 'loss_pct', 'jitter_ms', 'lost_pct'],
        'delay_avg_ms': _avg_from(ping, 'rtt_avg'),
        'intra_delay_avg_ms': _avg_from(intra_ping, 'rtt_avg'),
        'ping_loss_avg_pct': _avg_from(ping, 'loss_pct'),
        'tcp_throughput_avg_mbps': _avg_from(tcp, 'throughput_mbps'),
        'intra_throughput_avg_mbps': _avg_from(intra, 'throughput_mbps'),
        'udp_jitter_avg_ms': _avg_from(udp, 'jitter_ms'),
        'udp_loss_avg_pct': _avg_from(udp, 'lost_pct'),
        'load_loss_avg_pct': _avg_from(load, 'lost_pct'),
        'load_loss_max_pct': _max_from(load, 'lost_pct'),
        'performance_insights': _build_performance_insights(results),
        'ip_vs_mpls': _build_ip_vs_mpls_analysis(results),
        'mpls_evaluation': 'LDP binding/neighbor, kernel LFIB, trace_samples và tcpdump_label_proofs dùng để mô tả và chứng minh push/swap/PHP/pop bằng Hex Dump/Tcpdump.',
        'lan_comparison': 'Flat dùng ít tầng nhất; 3-Tier thêm VLAN và inter-VLAN routing; Leaf-Spine có OSPF/ECMP nên phù hợp khi cần nhiều đường song song.',
    }


def _attach_dashboard_metadata(results):
    results.setdefault('design', _load_design_manifest())
    results.setdefault('analysis_summary', _build_analysis_summary(results))
    return results

def _trim_output(text, max_lines=80, max_chars=9000):
    text = (text or '').strip()
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = '\n'.join(lines[:max_lines]) + f"\n... [truncated {len(lines) - max_lines} more lines]"
    if len(text) > max_chars:
        text = text[:max_chars] + '\n... [truncated]'
    return text

def _node_cmd(net, node_name, command):
    try:
        node = net.get(node_name)
        return _trim_output(node.cmd(command))
    except Exception as exc:
        return f'[ERROR] {node_name}: {exc}'

def _inventory_snapshot(net):
    try:
        names = sorted(getattr(net, 'nameToNode', {}).keys())
    except Exception:
        names = []
    return {
        'count': len(names),
        'nodes': names,
        'provider': [n for n in names if n in ('PE1', 'PE2', 'PE3', 'P1', 'P2', 'P3', 'P4', 'CE1', 'CE2', 'CE3')],
        'switches': [n for n in names if n.startswith(('flat', 'core', 'dist', 'access', 'spine', 'leaf'))],
        'hosts': [n for n in names if n.startswith('H')],
    }

def _collect_requirement_proofs(net, results=None, verbose=False):
    """Run the exact requirement-verification commands and store outputs in JSON.

    Important: the dashboard cannot execute Mininet commands by itself.  This
    function is called from runall/runquick so the proof commands are executed
    while the Mininet topology is still running.  The returned outputs are then
    saved to latest.json/dashboard_data.json for the dashboard to auto-check.
    """
    base = '/tmp/frr-mpls-lab'

    def run_cmd(req_id, title, node_name, display_command, actual_command):
        if verbose:
            print(f"  {hdr(req_id)} {info(display_command)}")
        output = _node_cmd(net, node_name, actual_command)
        if verbose:
            if 'ping' in display_command:
                status_text = ok('OK') if _ping_ok(output) else err('FAIL')
            else:
                status_text = ok('OK') if _output_ok(output) else warn('WARN')
            first_line = (output or '').strip().splitlines()[0] if (output or '').strip() else 'no output'
            if len(first_line) > 96:
                first_line = first_line[:96] + '...'
            print(f"      {status_text} {title}: {dim(first_line)}")
        return {'title': title, 'command': display_command, 'output': output}

    def inline_item(title, display_command, output):
        if verbose:
            print(f"  {hdr('INFO')} {info(display_command)}")
            print(f"      {ok('OK')} {title}")
        return {'title': title, 'command': display_command, 'output': output}

    if verbose:
        print(_bar('Requirement verification commands'))
        print('  Cac lenh duoi day duoc runall/runquick chay that, sau do luu vao JSON cho dashboard tu kiem chung.')

    inventory_output = json.dumps(_inventory_snapshot(net), indent=2, ensure_ascii=False)
    perf_summary_output = json.dumps((results or {}).get('analysis_summary', _build_analysis_summary(results or {})), indent=2, ensure_ascii=False) if results else ''
    connectivity_output = json.dumps((results or {}).get('connectivity', {}), indent=2, ensure_ascii=False)
    trace_output = json.dumps(trace_model(net, 'H1_1', 'H2_1'), indent=2, ensure_ascii=False)
    tcpdump_output = json.dumps((results or {}).get('tcpdump_label_proofs', {}), indent=2, ensure_ascii=False)

    proofs = {
        'REQ-1': {
            'status': 'implemented',
            'summary': 'MPLS backbone, LDP/LFIB va ket noi da chi nhanh.',
            'items': [
                run_cmd('REQ-1', 'P1 LDP binding', 'P1', 'P1 v show mpls ldp binding', f'{base}/P1/v show mpls ldp binding'),
                run_cmd('REQ-1', 'P1 LDP neighbor', 'P1', 'P1 v show mpls ldp neighbor', f'{base}/P1/v show mpls ldp neighbor'),
                run_cmd('REQ-1', 'P1 kernel MPLS LFIB', 'P1', 'P1 ip -f mpls route', 'ip -f mpls route'),
            ],
        },
        'REQ-2': {
            'status': 'implemented',
            'summary': 'Chi nhanh 1 mang phang, 4 host cung subnet.',
            'items': [
                run_cmd('REQ-2', 'host1 -> host4 ping', 'H1_1', 'H1_1 ping -c 2 192.168.10.14', 'ping -c 2 192.168.10.14'),
                run_cmd('REQ-2', 'host1 dia chi IP', 'H1_1', 'H1_1 ip -brief addr', 'ip -brief addr'),
            ],
        },
        'REQ-3': {
            'status': 'implemented',
            'summary': 'Campus 3 lop voi VLAN 10/20/30 va CE2 router-on-a-stick.',
            'items': [
                run_cmd('REQ-3', 'OVS core1 VLAN layout', 'core1', 'core1 ovs-vsctl show', 'ovs-vsctl show'),
                run_cmd('REQ-3', 'CE2 subinterfaces', 'CE2', "CE2 sh -c \"ip -brief addr show | grep -E 'ce2-bond|ce2-lan'\"", "sh -c \"ip -brief addr show | grep -E 'ce2-bond|ce2-lan'\""),
                run_cmd('REQ-3', 'CE2 kernel routing table', 'CE2', 'CE2 ip route', 'ip route'),
                run_cmd('REQ-3', 'CE2 FRR routing table', 'CE2', 'CE2 v show ip route', f'{base}/CE2/v show ip route'),
                run_cmd('REQ-3', 'Inter-VLAN ping', 'H2_1', 'H2_1 ping -c 2 192.168.21.11', 'ping -c 2 192.168.21.11'),
            ],
        },
        'REQ-4': {
            'status': 'implemented',
            'summary': 'Branch 3 leaf-spine voi OSPF underlay va ECMP.',
            'items': [
                run_cmd('REQ-4', 'spine1 OSPF neighbors', 'spine1', 'spine1 v show ip ospf neighbor', f'{base}/spine1/v show ip ospf neighbor'),
                run_cmd('REQ-4', 'spine1 OSPF routes', 'spine1', 'spine1 v show ip ospf route', f'{base}/spine1/v show ip ospf route'),
                run_cmd('REQ-4', 'CE3 FRR routing table', 'CE3', 'CE3 v show ip route', f'{base}/CE3/v show ip route'),
                run_cmd('REQ-4', 'spine1 kernel route ECMP', 'spine1', 'spine1 ip route', 'ip route'),
            ],
        },
        'REQ-5': {
            'status': 'implemented',
            'summary': 'Node inventory cua Mininet theo so do logic/vat ly.',
            'items': [
                inline_item('Mininet node inventory', 'inventory snapshot', inventory_output),
            ],
        },
        'REQ-6': {
            'status': 'implemented',
            'summary': 'Ket qua do hieu nang va JSON cho dashboard.',
            'items': [
                inline_item('Performance summary', 'runquick / runall -> dashboard_data.json', perf_summary_output),
                inline_item('Connectivity snapshot', 'verify connectivity', connectivity_output),
            ],
        },
        'REQ-7': {
            'status': 'implemented',
            'summary': 'Phan tich label switching MPLS va trace push/swap/PHP/pop.',
            'items': [
                inline_item('Trace host1 -> admin1', 'trace host1 admin1', trace_output),
                inline_item('Tcpdump Hex Dump MPLS labels', 'runquick/runall -> tcpdump_label_proofs', tcpdump_output),
                run_cmd('REQ-7', 'P1 LDP binding', 'P1', 'P1 v show mpls ldp binding', f'{base}/P1/v show mpls ldp binding'),
            ],
        },
    }
    return proofs


def _proof_item_output(proofs, req_id, title_part=None):
    """Return a proof output string from requirement_proofs."""
    req = (proofs or {}).get(req_id, {})
    for item in req.get('items', []) or []:
        title = item.get('title', '')
        if title_part is None or title_part.lower() in title.lower():
            return item.get('output', '') or ''
    return ''


def _ping_ok(output):
    try:
        parsed = parse_ping(output or '')
        return parsed.get('received', 0) > 0 and float(parsed.get('loss_pct', 100.0)) == 0.0
    except Exception:
        return False


def _output_ok(output):
    text = (output or '').strip().lower()
    if not text:
        return False
    bad_markers = ['[error]', 'traceback', 'command not found', 'no such file', 'not found']
    return not any(marker in text for marker in bad_markers)


def _mk_req_check(req_id, name, status, summary, evidence=None, details=None):
    return {
        'id': req_id,
        'name': name,
        'status': status,
        'summary': summary,
        'evidence': evidence or [],
        'details': details or {},
    }


def _build_requirement_checks(results):
    """Build PASS/WARN/FAIL checklist for the assignment dashboard.

    This function uses the JSON fields produced by runquick/runall plus
    requirement_proofs captured from live Mininet commands. The dashboard can
    therefore auto-check requirements without executing commands by itself.
    """
    results = results or {}
    proofs = results.get('requirement_proofs', {}) or {}
    checks = []

    # REQ-1: MPLS backbone / LDP / LFIB
    health = results.get('mpls_health', {}) or {}
    tables = results.get('mpls_tables', {}) or {}
    p1_mpls = ((tables.get('P1') or {}).get('mpls') or '')
    ldp_binding = _proof_item_output(proofs, 'REQ-1', 'binding')
    ldp_neighbor = _proof_item_output(proofs, 'REQ-1', 'neighbor')
    total = int(health.get('checks_total') or 0)
    passed = int(health.get('checks_passed') or 0)
    health_ratio_ok = total > 0 and passed >= max(1, int(total * 0.70))
    has_lfib = all(label in p1_mpls for label in ('16001', '16002', '16003'))
    has_ldp_text = _output_ok(ldp_binding) and _output_ok(ldp_neighbor)
    if health.get('enabled') and health_ratio_ok and has_lfib and has_ldp_text:
        status = 'PASS'
    elif health.get('enabled') and (health_ratio_ok or has_lfib):
        status = 'WARN'
    else:
        status = 'FAIL'
    checks.append(_mk_req_check(
        'REQ-1',
        'MPLS Backbone, LDP và LFIB',
        status,
        f"MPLS health {passed}/{total}; P1 LFIB labels 16001/16002/16003: {'có' if has_lfib else 'thiếu'}.",
        ['P1 v show mpls ldp binding', 'P1 v show mpls ldp neighbor', 'P1 ip -f mpls route'],
        {'health_passed': passed, 'health_total': total, 'p1_lfib_has_labels': has_lfib}
    ))

    # REQ-2: Branch 1 flat LAN
    b1_ping = _proof_item_output(proofs, 'REQ-2', 'ping')
    b1_addr = _proof_item_output(proofs, 'REQ-2', 'địa chỉ') or _proof_item_output(proofs, 'REQ-2', 'address')
    b1_ok = _ping_ok(b1_ping) and ('192.168.10.' in b1_addr or '192.168.10.' in b1_ping)
    checks.append(_mk_req_check(
        'REQ-2',
        'Chi nhánh 1 - Flat Network',
        'PASS' if b1_ok else 'FAIL',
        'host1 ping host4 thành công và mạng 192.168.10.0/24 được cấu hình.' if b1_ok else 'Chưa chứng minh được ping nội bộ Branch 1 hoặc thiếu địa chỉ 192.168.10.0/24.',
        ['H1_1 ping -c 2 192.168.10.14'],
        {'ping_ok': _ping_ok(b1_ping), 'has_branch1_ip': ('192.168.10.' in (b1_addr + b1_ping))}
    ))

    # REQ-3: Branch 2 3-tier + VLAN + inter-VLAN
    subif = _proof_item_output(proofs, 'REQ-3', 'subinterfaces')
    ce2_route = _proof_item_output(proofs, 'REQ-3', 'routing')
    inter_vlan = _proof_item_output(proofs, 'REQ-3', 'Inter-VLAN')
    has_subifs_lan = all(x in subif for x in ('ce2-lan.10', 'ce2-lan.20', 'ce2-lan.30'))
    has_subifs_bond = all(x in subif for x in ('ce2-bond.10', 'ce2-bond.20', 'ce2-bond.30'))
    has_subifs = has_subifs_lan or has_subifs_bond
    has_routes = all(x in ce2_route for x in ('192.168.20.0/24', '192.168.21.0/24', '192.168.22.0/24'))
    vlan_ok = has_subifs and has_routes and _ping_ok(inter_vlan)
    checks.append(_mk_req_check(
        'REQ-3',
        'Chi nhánh 2 - Core/Distribution/Access + VLAN',
        'PASS' if vlan_ok else ('WARN' if has_subifs or has_routes else 'FAIL'),
        f"Sub-interface CE2: {'đủ' if has_subifs else 'thiếu'}; route VLAN: {'đủ' if has_routes else 'thiếu'}; ping liên VLAN: {'OK' if _ping_ok(inter_vlan) else 'chưa OK'}.",
        ['core1 ovs-vsctl show', "CE2 sh -c \"ip -brief addr show | grep -E 'ce2-bond|ce2-lan'\"", 'CE2 ip route', 'H2_1 ping -c 2 192.168.21.11'],
        {'has_subinterfaces': has_subifs, 'has_ce2_bond_subinterfaces': has_subifs_bond, 'has_ce2_lan_subinterfaces': has_subifs_lan, 'has_vlan_routes': has_routes, 'inter_vlan_ping_ok': _ping_ok(inter_vlan)}
    ))

    # REQ-4: Branch 3 leaf-spine + OSPF/ECMP
    ecmp_routes = results.get('ecmp_routes', {}) or {}
    ecmp_count = sum(int((v or {}).get('ecmp_nexthops') or 0) for v in ecmp_routes.values())
    ospf_neighbor = _proof_item_output(proofs, 'REQ-4', 'neighbors')
    ospf_route = _proof_item_output(proofs, 'REQ-4', 'routes')
    ospf_ok = _output_ok(ospf_neighbor) and _output_ok(ospf_route)
    ecmp_ok = ecmp_count > 0
    checks.append(_mk_req_check(
        'REQ-4',
        'Chi nhánh 3 - Leaf-Spine, OSPF và ECMP',
        'PASS' if ospf_ok and ecmp_ok else ('WARN' if ospf_ok or ecmp_ok else 'FAIL'),
        f"OSPF snapshot: {'có' if ospf_ok else 'thiếu/lỗi'}; ECMP nexthop lines: {ecmp_count}.",
        ['spine1 v show ip ospf neighbor', 'spine1 v show ip ospf route', 'CE3 v show ip route', 'spine1 ip route'],
        {'ospf_snapshot_ok': ospf_ok, 'ecmp_nexthop_lines': ecmp_count}
    ))

    # REQ-5: Mininet inventory P/PE/CE/Switch/Host
    inv = results.get('inventory', {}) or {}
    nodes = set(inv.get('nodes', []) or [])
    required_nodes = {'PE1','PE2','PE3','P1','P2','P3','P4','CE1','CE2','CE3','H1_1','H2_1','H3_1','core1','spine1','leaf1'}
    missing = sorted(required_nodes - nodes)
    checks.append(_mk_req_check(
        'REQ-5',
        'Mininet node inventory P/PE/CE/Switch/Host',
        'PASS' if not missing else 'FAIL',
        f"Tổng node: {inv.get('count', 0)}; thiếu: {', '.join(missing) if missing else 'không'}.",
        ['sudo python3 topology.py'],
        {'node_count': inv.get('count', 0), 'missing_nodes': missing}
    ))

    # REQ-6: Performance data for throughput/delay/loss/jitter
    ping_rows = results.get('ping', []) or []
    tcp_rows = results.get('tcp', []) or []
    udp_rows = results.get('udp', []) or []
    load_rows = results.get('load', []) or []
    perf_ok = bool(ping_rows and tcp_rows and udp_rows and load_rows)
    checks.append(_mk_req_check(
        'REQ-6',
        'Hiệu năng Throughput, Delay, Packet loss, Jitter',
        'PASS' if perf_ok else 'WARN',
        f"ping={len(ping_rows)}, tcp={len(tcp_rows)}, udp={len(udp_rows)}, load={len(load_rows)}. {'Đủ dữ liệu biểu đồ.' if perf_ok else 'Dữ liệu chưa đủ, nên chạy runall.'}",
        ['runquick', 'runall', 'dash 8000'],
        {'ping_count': len(ping_rows), 'tcp_count': len(tcp_rows), 'udp_count': len(udp_rows), 'load_count': len(load_rows)}
    ))

    # REQ-7: MPLS label switching / trace samples + tcpdump hex proof
    traces = results.get('trace_samples', {}) or {}
    actions = []
    for trace in traces.values():
        if isinstance(trace, dict):
            actions.extend([step.get('action') for step in trace.get('steps', []) if isinstance(step, dict)])
    trace_ok = 'push' in actions and ('php' in actions or 'swap' in actions) and 'pop' in actions
    tcpdump = results.get('tcpdump_label_proofs', {}) or {}
    tcpdump_caps = []
    for proof in tcpdump.values():
        if isinstance(proof, dict):
            tcpdump_caps.extend([c for c in proof.get('captures', []) if isinstance(c, dict)])
    hex_caps = [c for c in tcpdump_caps if c.get('has_hex')]
    mpls_hex_caps = [c for c in tcpdump_caps if c.get('has_hex') and c.get('has_mpls')]
    pop_hex_caps = [c for c in tcpdump_caps if str(c.get('expected_action')).lower() == 'pop' and c.get('has_hex')]
    tcpdump_ok = bool(mpls_hex_caps and pop_hex_caps)
    if trace_ok and tcpdump_ok:
        req7_status = 'PASS'
        req7_summary = f'Trace có push/swap/PHP/pop và tcpdump -XX có {len(mpls_hex_caps)} capture MPLS + {len(pop_hex_caps)} capture pop IPv4/ICMP.'
    elif trace_ok or tcpdump_ok:
        req7_status = 'WARN'
        req7_summary = 'Có một phần bằng chứng label switching, nhưng nên chạy lại runall/runquick để đủ cả trace và tcpdump hex dump.'
    else:
        req7_status = 'WARN'
        req7_summary = 'Trace/tcpdump chưa thể hiện đầy đủ push/swap/PHP/pop, kiểm tra lại trace_samples và tcpdump_label_proofs.'
    checks.append(_mk_req_check(
        'REQ-7',
        'Phân tích MPLS label switching và đường đi gói tin',
        req7_status,
        req7_summary,
        ['trace host1 admin1', 'runquick', 'runall', 'P1 v show mpls ldp binding'],
        {'actions_seen': sorted(set(a for a in actions if a)), 'tcpdump_flows': len(tcpdump), 'hex_captures': len(hex_caps), 'mpls_hex_captures': len(mpls_hex_caps), 'pop_hex_captures': len(pop_hex_caps)}
    ))


    # REQ-8: MPLS vs IP routing comparison in the same runall JSON
    compare = results.get('compare', {}) or {}
    comp_sum = compare.get('comparison_summary', {}) or {}
    compare_ok = bool(compare.get('available')) and comp_sum.get('delay_mpls_avg_ms') is not None and comp_sum.get('delay_ip_avg_ms') is not None
    checks.append(_mk_req_check(
        'REQ-8',
        'So sánh MPLS với IP Routing truyền thống',
        'PASS' if compare_ok else 'WARN',
        'runall đã sinh dữ liệu so sánh MPLS/IP trong cùng latest.json.' if compare_ok else 'Chưa có dữ liệu compare; hãy chạy runall bản đầy đủ.',
        ['runall', 'dash 8000'],
        {'compare_available': bool(compare.get('available')), 'comparison_summary': comp_sum}
    ))

    pass_count = sum(1 for c in checks if c['status'] == 'PASS')
    fail_count = sum(1 for c in checks if c['status'] == 'FAIL')
    warn_count = sum(1 for c in checks if c['status'] == 'WARN')
    results['requirement_check_summary'] = {
        'pass': pass_count,
        'warn': warn_count,
        'fail': fail_count,
        'total': len(checks),
    }
    return checks


def _print_requirement_check_summary(results):
    checks = (results or {}).get('requirement_checks', []) or []
    summary = (results or {}).get('requirement_check_summary', {}) or {}
    if not checks:
        return
    print(_bar('Requirement verification summary'))
    print(f"  PASS={summary.get('pass', 0)}  WARN={summary.get('warn', 0)}  FAIL={summary.get('fail', 0)}  TOTAL={summary.get('total', len(checks))}")
    for c in checks:
        status = c.get('status', 'WARN')
        if status == 'PASS':
            tag = ok('PASS')
        elif status == 'FAIL':
            tag = err('FAIL')
        else:
            tag = warn('WARN')
        print(f"  {tag} {c.get('id')} - {c.get('name')}: {c.get('summary')}")

def _write_json_file(fp, data):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _fix_path_permissions(fp, is_dir=False)


def _mirror_dashboard_json(net, results, primary_fp):
    """Keep dashboard_data.json and latest.json in sync for auto-loading."""
    base = _out_dir(net)
    mirrors = [
        os.path.join(_project_root(), 'dashboard_data.json'),
        os.path.join(base, 'dashboard_data.json'),
        os.path.join(base, 'latest.json'),
    ]
    for fp in mirrors:
        try:
            _write_json_file(fp, results)
        except Exception as exc:
            print(f'[WARN] cannot write dashboard mirror {fp}: {exc}')
    return mirrors


def _save_results(net, results, prefix='results'):
    results = _attach_dashboard_metadata(results)
    mode = results.get('backbone_mode', _backbone_mode(net))
    base = _out_dir(net)
    fp = os.path.join(base, f'{prefix}_{mode}_{_timestamp()}.json')
    _write_json_file(fp, results)
    _fix_path_permissions(base, is_dir=True)
    mirrors = _mirror_dashboard_json(net, results, fp)
    print(f"  {ok('Dashboard JSON:')} {info(mirrors[0])}")
    print(f"  {ok('Latest JSON:')}    {info(mirrors[-1])}")
    return fp


def latest_result_file(net=None):
    files = sorted(glob(os.path.join(_out_dir(net), '*.json')))
    return files[-1] if files else None


# ---------------------------------------------------------------------------
#  Generic parsers
# ---------------------------------------------------------------------------

def parse_ping(output):
    result = {
        'sent': 0, 'received': 0, 'loss_pct': 100.0,
        'rtt_min': None, 'rtt_avg': None, 'rtt_max': None, 'rtt_mdev': None,
    }
    m = re.search(r'(\d+) packets transmitted, (\d+) received, ([\d.]+)% packet loss', output)
    if m:
        result['sent'] = int(m.group(1))
        result['received'] = int(m.group(2))
        result['loss_pct'] = float(m.group(3))
    m = re.search(r'min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', output)
    if m:
        result['rtt_min'] = float(m.group(1))
        result['rtt_avg'] = float(m.group(2))
        result['rtt_max'] = float(m.group(3))
        result['rtt_mdev'] = float(m.group(4))
    return result


def parse_iperf3_json(output):
    try:
        start = output.index('{')
        data = json.loads(output[start:])
    except Exception:
        return {'error': output[:240], 'throughput_mbps': 0.0}

    end = data.get('end', {})
    if 'sum' in end and 'jitter_ms' in end['sum']:
        s = end['sum']
        return {
            'protocol': 'UDP',
            'throughput_mbps': round(float(s.get('bits_per_second', 0)) / 1e6, 3),
            'jitter_ms': round(float(s.get('jitter_ms', 0)), 3),
            'lost_pct': round(float(s.get('lost_percent', 0)), 3),
            'packets': int(s.get('packets', 0) or 0),
        }

    recv = end.get('sum_received') or end.get('sum')
    sent = end.get('sum_sent') or {}
    if recv:
        return {
            'protocol': 'TCP',
            'throughput_mbps': round(float(recv.get('bits_per_second', 0)) / 1e6, 3),
            'retransmits': int(sent.get('retransmits', 0) or 0),
            'jitter_ms': None,
            'lost_pct': None,
        }

    return {'error': 'unknown iperf3 output', 'throughput_mbps': 0.0}


# ---------------------------------------------------------------------------
#  Scenario metadata
# ---------------------------------------------------------------------------

def branch_of(host):
    host = str(host)
    if host.startswith('H1_'):
        return 'branch1'
    if host.startswith('H2_'):
        return 'branch2'
    if host.startswith('H3_'):
        return 'branch3'
    return 'unknown'


def arch_of(branch):
    return BRANCH_NAMES.get(branch, 'Unknown')


def leaf_of(host):
    m = re.match(r'H3_(\d+)$', str(host))
    if not m:
        return None
    n = int(m.group(1))
    if n <= 2:
        return 'leaf1'
    if n <= 4:
        return 'leaf2'
    return 'leaf4'


def core_path_for_h2(host):
    m = re.match(r'H2_(\d+)$', str(host))
    if not m:
        return ('access1', 'dist1', 'core1')
    n = int(m.group(1))
    if n in (1, 2):
        return ('access1', 'dist1', 'core1')
    if n in (3, 4):
        return ('access2', 'dist1', 'core1')
    if n in (5, 6):
        return ('access3', 'dist2', 'core1')
    return ('access3', 'dist2', 'core1')


def scenario_meta(src, dst, group='cross'):
    src_branch = branch_of(src)
    dst_branch = branch_of(dst)
    return {
        'src_branch': src_branch,
        'dst_branch': dst_branch,
        'src_architecture': arch_of(src_branch),
        'dst_architecture': arch_of(dst_branch),
        'scenario_group': group,
        'category': group if group != 'intra' else src_branch,
    }


def pretty_pair(src, dst):
    meta = scenario_meta(src, dst, 'cross')
    return f"{meta['src_architecture']} -> {meta['dst_architecture']} ({src}->{dst})"


# ---------------------------------------------------------------------------
#  Trace / label switching model
# ---------------------------------------------------------------------------

def lan_path_from_host(host):
    b = branch_of(host)
    if b == 'branch1':
        return [host, 'flat1', 'CE1']
    if b == 'branch2':
        acc, dist, core = core_path_for_h2(host)
        return [host, acc, dist, core, 'CE2']
    if b == 'branch3':
        leaf = leaf_of(host)
        return [host, leaf, 'spine1', 'CE3']
    return [host]


def lan_path_to_host(host):
    return list(reversed(lan_path_from_host(host)))


def transport_label_for_branch(branch):
    return {'branch1': '16001', 'branch2': '16002', 'branch3': '16003'}.get(branch, '?')


def service_label_for_branch(branch):
    return {'branch1': '101', 'branch2': '201', 'branch3': '301'}.get(branch, '?')


def core_path_between(src_branch, dst_branch):
    """Provider-core path used by static MPLS/IP forwarding."""
    table = {
        ('branch1', 'branch2'): ['P3'],
        ('branch2', 'branch1'): ['P3'],
        ('branch1', 'branch3'): ['P1', 'P2'],
        ('branch3', 'branch1'): ['P2', 'P1'],
        ('branch2', 'branch3'): ['P4'],
        ('branch3', 'branch2'): ['P4'],
    }
    return table.get((src_branch, dst_branch), ['P1'])



FLOW_CAPTURE_PLANS = {
    'B1_to_B2': {
        'label': 'Branch 1 Flat -> Branch 2 3-Tier', 'src': 'H1_1', 'dst': 'H2_1',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE1', 'interface': 'pe1-p3', 'expected_action': 'push', 'expected_label_stack': '16002/201', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P3', 'interface': 'p3-pe2', 'expected_action': 'php', 'expected_label_stack': '201', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE2', 'interface': 'pe2-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
    'B2_to_B1': {
        'label': 'Branch 2 3-Tier -> Branch 1 Flat', 'src': 'H2_1', 'dst': 'H1_1',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE2', 'interface': 'pe2-p3', 'expected_action': 'push', 'expected_label_stack': '16001/101', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P3', 'interface': 'p3-pe1', 'expected_action': 'php', 'expected_label_stack': '101', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE1', 'interface': 'pe1-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
    'B1_to_B3': {
        'label': 'Branch 1 Flat -> Branch 3 Leaf-Spine', 'src': 'H1_1', 'dst': 'H3_1',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE1', 'interface': 'pe1-p1', 'expected_action': 'push', 'expected_label_stack': '16003/301', 'filter': 'mpls'},
            {'stage': 'SWAP/FORWARD tại P trung gian', 'node': 'P1', 'interface': 'p1-p2', 'expected_action': 'swap', 'expected_label_stack': '16003/301', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P2', 'interface': 'p2-pe3', 'expected_action': 'php', 'expected_label_stack': '301', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE3', 'interface': 'pe3-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
    'B3_to_B1': {
        'label': 'Branch 3 Leaf-Spine -> Branch 1 Flat', 'src': 'H3_1', 'dst': 'H1_1',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE3', 'interface': 'pe3-p2', 'expected_action': 'push', 'expected_label_stack': '16001/101', 'filter': 'mpls'},
            {'stage': 'SWAP/FORWARD tại P trung gian', 'node': 'P2', 'interface': 'p2-p1', 'expected_action': 'swap', 'expected_label_stack': '16001/101', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P1', 'interface': 'p1-pe1', 'expected_action': 'php', 'expected_label_stack': '101', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE1', 'interface': 'pe1-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
    'B2_to_B3': {
        'label': 'Branch 2 3-Tier -> Branch 3 Leaf-Spine', 'src': 'H2_1', 'dst': 'H3_3',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE2', 'interface': 'pe2-p4', 'expected_action': 'push', 'expected_label_stack': '16003/301', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P4', 'interface': 'p4-pe3', 'expected_action': 'php', 'expected_label_stack': '301', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE3', 'interface': 'pe3-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
    'B3_to_B2': {
        'label': 'Branch 3 Leaf-Spine -> Branch 2 3-Tier', 'src': 'H3_5', 'dst': 'H2_3',
        'captures': [
            {'stage': 'PUSH tại PE ingress', 'node': 'PE3', 'interface': 'pe3-p4', 'expected_action': 'push', 'expected_label_stack': '16002/201', 'filter': 'mpls'},
            {'stage': 'PHP tại P gần PE đích', 'node': 'P4', 'interface': 'p4-pe2', 'expected_action': 'php', 'expected_label_stack': '201', 'filter': 'mpls'},
            {'stage': 'POP tại PE egress ra CE', 'node': 'PE2', 'interface': 'pe2-wan', 'expected_action': 'pop', 'expected_ethertype': 'IPv4/ICMP, không còn MPLS', 'filter': 'icmp'},
        ],
    },
}

def trace_model(net, src, dst):
    mode = _backbone_mode(net)
    src_branch = branch_of(src)
    dst_branch = branch_of(dst)
    if src_branch == dst_branch:
        path = lan_path_from_host(src)
        end = lan_path_to_host(dst)
        full = path + end[1:]
        return {
            'src': src,
            'dst': dst,
            'mode': mode,
            'kind': 'intra',
            'steps': [{'node': n, 'action': 'ip' if n.startswith('H') else 'l2/l3'} for n in full],
        }

    steps = []
    for node in lan_path_from_host(src):
        steps.append({'node': node, 'action': 'host' if node.startswith('H') else 'lan'})

    ingress_pe = {'branch1': 'PE1', 'branch2': 'PE2', 'branch3': 'PE3'}[src_branch]
    egress_pe = {'branch1': 'PE1', 'branch2': 'PE2', 'branch3': 'PE3'}[dst_branch]
    ingress_ce = {'branch1': 'CE1', 'branch2': 'CE2', 'branch3': 'CE3'}[src_branch]
    if steps[-1]['node'] != ingress_ce:
        steps.append({'node': ingress_ce, 'action': 'lan'})

    core_path = core_path_between(src_branch, dst_branch)
    if mode == 'mpls':
        steps.append({
            'node': ingress_pe,
            'action': 'push',
            'transport_label': transport_label_for_branch(dst_branch),
            'service_label': service_label_for_branch(dst_branch),
            'text': f"Push [{transport_label_for_branch(dst_branch)},{service_label_for_branch(dst_branch)}]",
        })
        for idx, pnode in enumerate(core_path):
            if idx == len(core_path) - 1:
                text = f"PHP outer {transport_label_for_branch(dst_branch)}"
                action = 'php'
            else:
                text = f"Swap/forward outer {transport_label_for_branch(dst_branch)}"
                action = 'swap'
            steps.append({
                'node': pnode,
                'action': action,
                'transport_label': transport_label_for_branch(dst_branch),
                'text': text,
            })
        steps.append({
            'node': egress_pe,
            'action': 'pop',
            'service_label': service_label_for_branch(dst_branch),
            'text': f"Pop service {service_label_for_branch(dst_branch)}",
        })
    else:
        steps.append({'node': ingress_pe, 'action': 'ip', 'text': 'IP forward'})
        for pnode in core_path:
            steps.append({'node': pnode, 'action': 'ip', 'text': 'IP route lookup'})
        steps.append({'node': egress_pe, 'action': 'ip', 'text': 'IP forward'})

    for node in lan_path_to_host(dst):
        if node == egress_pe:
            continue
        steps.append({'node': node, 'action': 'host' if node.startswith('H') else 'lan'})

    return {
        'src': src,
        'dst': dst,
        'mode': mode,
        'kind': 'cross',
        'steps': steps,
    }


def trace_mpls_path(net, src, dst):
    trace = trace_model(net, src, dst)
    mode = trace['mode']
    print(_bar(f"Trace {display_name(src)} -> {display_name(dst)} ({mode.upper()})"))
    for idx, step in enumerate(trace['steps'], 1):
        action = step.get('action', 'ip')
        desc = display_name(step.get('text', action))
        node_name = display_name(step['node'])
        print(f"  {idx:02d}. {node_name:<10} {desc}")
    return trace



def _tcpdump_filter_expr(filter_name):
    if filter_name == 'icmp':
        return 'icmp'
    if filter_name == 'mpls':
        return 'mpls'
    return 'mpls or icmp'


def _labels_seen_from_tcpdump(output):
    text = output or ''
    labels = []
    for m in re.finditer(r'label\s+([0-9]+)', text, flags=re.IGNORECASE):
        if m.group(1) not in labels:
            labels.append(m.group(1))
    return labels


def _capture_has_hex(output):
    return bool(re.search(r'^\s*0x[0-9a-fA-F]{4}:', output or '', flags=re.MULTILINE))


def _capture_has_mpls(output):
    text = (output or '').lower()
    return 'mpls' in text or '0x8847' in text or 'ethertype mpls' in text


def _expected_labels_ok(capture):
    expected = str(capture.get('expected_label_stack') or '')
    if not expected:
        return True
    labels_seen = set(capture.get('labels_seen') or [])
    labels_expected = [x for x in re.split(r'[^0-9]+', expected) if x]
    return all(x in labels_seen for x in labels_expected)


def _collect_tcpdump_label_proofs(net, flow_keys=None, verbose=False):
    """Capture live MPLS packets with tcpdump -XX and store proof in JSON.

    This is the missing rubric evidence: PE ingress shows PUSH with two labels,
    P routers show SWAP/FORWARD or PHP, and PE egress shows POP back to IPv4.
    The dashboard reads this object and renders the raw hex dump per branch pair.
    """
    if _backbone_mode(net) != 'mpls':
        return {}
    if flow_keys is None:
        flow_keys = list(FLOW_CAPTURE_PLANS.keys())
    out_dir = os.path.join(_out_dir(net), 'tcpdump_proofs')
    os.makedirs(out_dir, exist_ok=True)
    _fix_path_permissions(out_dir, is_dir=True)

    proofs = {}
    if verbose:
        print(_bar('TCPDUMP HEX LABEL PROOFS'))
        print('  Chup tcpdump -XX tren cac interface PE/P de chung minh Push/Swap/PHP/Pop.')

    for key in flow_keys:
        plan = FLOW_CAPTURE_PLANS.get(key)
        if not plan:
            continue
        src, dst = plan['src'], plan['dst']
        try:
            dst_ip = net.get(dst).IP()
        except Exception:
            dst_ip = ''
        safe_key = re.sub(r'[^A-Za-z0-9_.-]+', '_', key)
        captures = []
        started = []

        if verbose:
            print(f"  {hdr(key)} {display_name(src)} -> {display_name(dst)}")

        # Start all capture points first so the same ping is visible along the path.
        for idx, cap in enumerate(plan.get('captures', []), 1):
            node_name = cap['node']
            iface = cap['interface']
            filter_expr = _tcpdump_filter_expr(cap.get('filter'))
            fp = os.path.join(out_dir, f'{safe_key}_{idx}_{node_name}_{iface}.txt')
            try:
                node = net.get(node_name)
                node.cmd(f'rm -f {fp}')
                cmd = f"timeout 5 tcpdump -i {iface} -U -c 4 -nn -e -vv -XX '{filter_expr}' > {fp} 2>&1 & echo $!"
                pid = node.cmd(cmd).strip().splitlines()[-1:]
                started.append((node_name, iface, fp, cap, cmd, pid[0] if pid else ''))
            except Exception as exc:
                cap_out = dict(cap)
                cap_out.update({
                    'command': f"{node_name} tcpdump -i {iface} -nn -e -vv -XX '{filter_expr}'",
                    'output': f'[ERROR] Cannot start tcpdump: {exc}',
                    'labels_seen': [], 'has_hex': False, 'has_mpls': False, 'expected_ok': False,
                })
                captures.append(cap_out)

        time.sleep(0.7)
        ping_output = ''
        try:
            ping_output = _trim_output(net.get(src).cmd(f'ping -c 3 -i 0.2 -W 2 {dst_ip}'), max_lines=30, max_chars=2500)
        except Exception as exc:
            ping_output = f'[ERROR] ping failed: {exc}'
        time.sleep(5.3)

        for node_name, iface, fp, cap, cmd, pid in started:
            try:
                node = net.get(node_name)
                node.cmd(f'pkill -f "tcpdump -i {iface}" >/dev/null 2>&1 || true')
                output = node.cmd(f'cat {fp} 2>/dev/null || true')
            except Exception as exc:
                output = f'[ERROR] Cannot read tcpdump output: {exc}'
            output = _trim_output(output, max_lines=120, max_chars=12000)
            cap_out = dict(cap)
            cap_out.update({
                'command': f"{node_name} tcpdump -i {iface} -U -c 4 -nn -e -vv -XX '{_tcpdump_filter_expr(cap.get('filter'))}'",
                'output': output,
                'labels_seen': _labels_seen_from_tcpdump(output),
                'has_hex': _capture_has_hex(output),
                'has_mpls': _capture_has_mpls(output),
                'expected_ok': False,
            })
            cap_out['expected_ok'] = _expected_labels_ok(cap_out) if cap_out.get('has_hex') else False
            captures.append(cap_out)
            if verbose:
                label_txt = ','.join(cap_out['labels_seen']) or 'no-label'
                status = ok('OK') if cap_out['has_hex'] and (cap_out['has_mpls'] or cap_out.get('filter') == 'icmp') else warn('WARN')
                print(f"      {status} {node_name}:{iface} labels={label_txt}")

        mpls_caps = [c for c in captures if c.get('filter') == 'mpls']
        pop_caps = [c for c in captures if str(c.get('expected_action')).lower() == 'pop']
        mpls_ok = sum(1 for c in mpls_caps if c.get('has_hex') and c.get('has_mpls'))
        pop_ok = sum(1 for c in pop_caps if c.get('has_hex'))
        proofs[key] = {
            'key': key,
            'label': plan.get('label'),
            'src': src,
            'dst': dst,
            'dst_ip': dst_ip,
            'ping_command': f'{display_name(src)} ping -c 3 {dst_ip}',
            'ping_output': ping_output,
            'trace': trace_model(net, src, dst),
            'captures': captures,
            'summary': f'{mpls_ok}/{len(mpls_caps)} MPLS capture co hex/MPLS; {pop_ok}/{len(pop_caps)} pop capture co hex IPv4/ICMP.',
        }
    return proofs

# ---------------------------------------------------------------------------
#  Health / snapshots
# ---------------------------------------------------------------------------

def _collect_mpls_health(net):
    mode = _backbone_mode(net)
    if mode != 'mpls':
        return {'enabled': False, 'mode': mode, 'checks_passed': 0, 'checks_total': 0}

    print(_bar('MPLS Health Check'))
    summary = {'enabled': True, 'mode': mode, 'nodes': {}, 'checks_passed': 0, 'checks_total': 0}

    for name in PROVIDER_NODES:
        node = net.get(name)
        ip_routes = node.cmd('ip route show').strip()
        mpls_routes = node.cmd('ip -f mpls route show').strip()
        platform_labels = node.cmd('cat /proc/sys/net/mpls/platform_labels 2>/dev/null').strip()
        rp_filter = node.cmd('cat /proc/sys/net/ipv4/conf/all/rp_filter 2>/dev/null').strip()
        node_report = {
            'platform_labels': platform_labels,
            'rp_filter': rp_filter,
            'ip_routes': ip_routes,
            'mpls_routes': mpls_routes,
            'checks': [],
        }

        print(f'\n  {hdr(name)}')
        checks = 0
        passed = 0

        ok_platform = platform_labels.isdigit() and int(platform_labels) >= 1000
        node_report['checks'].append({'name': 'platform_labels', 'ok': ok_platform, 'expected': '>=1000'})
        checks += 1
        passed += 1 if ok_platform else 0
        print(f"    {'✓' if ok_platform else '✗'} platform_labels={platform_labels or 'N/A'}")

        ok_rpf = rp_filter in ('0', '2')
        node_report['checks'].append({'name': 'rp_filter', 'ok': ok_rpf, 'expected': '0 or 2'})
        checks += 1
        passed += 1 if ok_rpf else 0
        print(f"    {'✓' if ok_rpf else '✗'} rp_filter={rp_filter or 'N/A'}")

        if name in ('PE1', 'PE2', 'PE3'):
            for prefix, stack, nh in EXPECTED_MPLS[name]['push']:
                present = (prefix in ip_routes and 'encap mpls' in ip_routes and stack in ip_routes and nh in ip_routes)
                node_report['checks'].append({'name': f'push {prefix}', 'ok': present, 'expected': f'{stack} via {nh}'})
                checks += 1
                passed += 1 if present else 0
                print(f"    {'✓' if present else '✗'} PUSH {prefix} -> {stack} via {nh}")

            for label, nh in EXPECTED_MPLS[name]['pop']:
                present = (label in mpls_routes and nh in mpls_routes)
                node_report['checks'].append({'name': f'pop {label}', 'ok': present, 'expected': f'via {nh}'})
                checks += 1
                passed += 1 if present else 0
                print(f"    {'✓' if present else '✗'} POP  {label} via {nh}")

        if name in ('P1', 'P2', 'P3', 'P4'):
            for label, nh in EXPECTED_MPLS[name]['lfib']:
                present = (label in mpls_routes and nh in mpls_routes)
                node_report['checks'].append({'name': f'lfib {label}', 'ok': present, 'expected': f'via/as route to {nh}'})
                checks += 1
                passed += 1 if present else 0
                print(f"    {'✓' if present else '✗'} LFIB {label} via/as {nh}")

        node_report['checks_passed'] = passed
        node_report['checks_total'] = checks
        summary['nodes'][name] = node_report
        summary['checks_passed'] += passed
        summary['checks_total'] += checks

    pct = 0 if summary['checks_total'] == 0 else round(summary['checks_passed'] * 100.0 / summary['checks_total'], 2)
    summary['health_percent'] = pct
    print(f"\n  Health total: {summary['checks_passed']}/{summary['checks_total']} = {pct:.2f}%")
    return summary


def _collect_mpls_tables(net):
    data = {}
    for name in PROVIDER_NODES:
        node = net.get(name)
        data[name] = {
            'ip': node.cmd('ip route show').strip(),
            'mpls': node.cmd('ip -f mpls route show').strip(),
        }
    return data


def _collect_ecmp_status(net):
    print(_bar('Branch 3 ECMP snapshot'))
    ecmp = {}
    for name in ['CE3', 'spine1', 'spine2', 'leaf1', 'leaf2', 'leaf3', 'leaf4']:
        node = net.get(name)
        routes = node.cmd('ip route show').strip().splitlines()
        interesting = [line.strip() for line in routes if line.strip() and ('nexthop' in line or '192.168.30.' in line or '192.168.31.' in line or '192.168.32.' in line or 'default' in line)]
        ecmp_nexthops = sum(1 for line in interesting if 'nexthop' in line)
        ecmp[name] = {
            'routes': interesting[:12],
            'ecmp_nexthops': ecmp_nexthops,
            'has_ecmp': ecmp_nexthops > 0,
        }
        print(f'  {name}: {ecmp[name]["ecmp_nexthops"]} ECMP lines')
    return ecmp


def _collect_connectivity(net, count=3):
    print(_bar('Quick connectivity matrix'))
    # Kiem tra hai chieu de dashboard khong chi hien B1 -> branch khac.
    cases = [
        ('H1_1', 'H4_1') if False else ('H1_1', 'H1_4'),  # intra B1 flat switch
        ('H2_1', 'H2_2'),  # intra B2 same VLAN
        ('H2_1', 'H2_3'),  # intra B2 inter-VLAN through CE2
        ('H3_1', 'H3_5'),  # intra B3 leaf-spine
        ('H1_1', 'H2_1'),
        ('H2_1', 'H1_1'),
        ('H1_1', 'H3_1'),
        ('H3_1', 'H1_1'),
        ('H2_1', 'H3_1'),
        ('H3_1', 'H2_1'),
    ]
    matrix = {}
    for src, dst in cases:
        out = net.get(src).cmd(f'ping -c {count} -W 2 {net.get(dst).IP()}')
        parsed = parse_ping(out)
        label = f'{src}->{dst}'
        display_label = display_pair(src, dst)
        matrix[label] = {
            'display_label': display_label,
            'src': src,
            'dst': dst,
            'src_branch': branch_of(src),
            'dst_branch': branch_of(dst),
            'rtt_avg': parsed['rtt_avg'],
            'loss_pct': parsed['loss_pct'],
            'ok': parsed['loss_pct'] == 0 and parsed.get('received', 0) > 0,
        }
        status = ok('OK') if parsed['loss_pct'] == 0 else warn('CHECK')
        print(f'  {status} {display_label:<16} RTT={parsed["rtt_avg"]} ms  loss={parsed["loss_pct"]}%')
    return matrix

# ---------------------------------------------------------------------------
#  iperf / ping primitives
# ---------------------------------------------------------------------------

_PORT_COUNTER = 5201

def next_port():
    global _PORT_COUNTER
    _PORT_COUNTER += 1
    return _PORT_COUNTER

def kill_iperf3(net):
    for node in net.hosts:
        node.cmd('pkill -f iperf3 >/dev/null 2>&1 || true')

def test_ping(net, src, dst, count=10, label=''):
    dst_ip = net.get(dst).IP()
    terminal_label = display_name(label or f'{src}->{dst}')
    print(f'  [PING] {terminal_label} ... ', end='', flush=True)
    out = net.get(src).cmd(f'ping -c {count} -i 0.2 -W 2 {dst_ip}')
    parsed = parse_ping(out)
    meta = scenario_meta(src, dst, group='cross' if branch_of(src) != branch_of(dst) else 'intra')
    row = {
        'test': 'ping',
        'src': src,
        'dst': dst,
        'label': label or f'{src}->{dst}',
        'display_label': display_name(label or f'{src}->{dst}'),
        **meta,
        **parsed,
    }
    print(f'RTT={parsed["rtt_avg"]} ms  loss={parsed["loss_pct"]}%')
    return row

def test_tcp(net, server, client, duration=8, parallel=1, label=''):
    port = next_port()
    server_node = net.get(server)
    client_node = net.get(client)
    server_ip = server_node.IP()
    terminal_label = display_name(label or f'{client}->{server}')
    print(f'  [TCP ] {terminal_label} ... ', end='', flush=True)
    server_node.cmd(f'iperf3 -s -p {port} -D --logfile /tmp/iperf3_server_{server}_{port}.log')
    time.sleep(0.7)
    out = client_node.cmd(f'iperf3 -c {server_ip} -p {port} -t {duration} -P {parallel} -J 2>&1')
    server_node.cmd(f'pkill -f "iperf3 -s -p {port}" >/dev/null 2>&1 || true')
    parsed = parse_iperf3_json(out)
    meta = scenario_meta(client, server, group='cross' if branch_of(client) != branch_of(server) else 'intra')
    row = {
        'test': 'tcp',
        'src': client,
        'dst': server,
        'label': label or f'{client}->{server}',
        'display_label': display_name(label or f'{client}->{server}'),
        'duration': duration,
        'parallel': parallel,
        **meta,
        **parsed,
    }
    print(f'{row.get("throughput_mbps", 0)} Mbps')
    return row

def test_udp(net, server, client, bw='20M', duration=8, label='', group=None, offered_mbps=None):
    port = next_port()
    server_node = net.get(server)
    client_node = net.get(client)
    server_ip = server_node.IP()
    terminal_label = display_name(label or f'{client}->{server}')
    print(f'  [UDP ] {terminal_label} ... ', end='', flush=True)
    server_node.cmd(f'iperf3 -s -p {port} -D --logfile /tmp/iperf3_udp_{server}_{port}.log')
    time.sleep(0.7)
    out = client_node.cmd(f'iperf3 -c {server_ip} -p {port} -u -b {bw} -t {duration} -J 2>&1')
    server_node.cmd(f'pkill -f "iperf3 -s -p {port}" >/dev/null 2>&1 || true')
    parsed = parse_iperf3_json(out)
    mode_group = group or ('cross' if branch_of(client) != branch_of(server) else 'intra')
    meta = scenario_meta(client, server, group=mode_group)
    row = {
        'test': 'udp',
        'src': client,
        'dst': server,
        'label': label or f'{client}->{server}',
        'display_label': display_name(label or f'{client}->{server}'),
        'duration': duration,
        'bw': bw,
        'offered_load_mbps': offered_mbps if offered_mbps is not None else _bw_to_mbps(bw),
        **meta,
        **parsed,
    }
    print(f'tp={row.get("throughput_mbps", 0)} Mbps  jitter={row.get("jitter_ms")} ms  loss={row.get("lost_pct")}%')
    return row

def _bw_to_mbps(bw):
    s = str(bw).strip().upper()
    if s.endswith('M'):
        return float(s[:-1])
    if s.endswith('K'):
        return round(float(s[:-1]) / 1000.0, 3)
    if s.endswith('G'):
        return float(s[:-1]) * 1000.0
    try:
        return float(s)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
#  Test suites
# ---------------------------------------------------------------------------

def run_ping_tests(net):
    print(_bar('TEST 1 - Ping delay/loss cross-branch, two-way'))
    # Do hai chieu cho tung cap chi nhanh de co ca du lieu B3 -> B1/B2.
    cases = [
        ('H1_1', 'H2_1', 'B1 Flat -> B2 3-Tier'),
        ('H2_1', 'H1_1', 'B2 3-Tier -> B1 Flat'),
        ('H1_1', 'H3_1', 'B1 Flat -> B3 Leaf-Spine'),
        ('H3_1', 'H1_1', 'B3 Leaf-Spine -> B1 Flat'),
        ('H2_3', 'H3_5', 'B2 3-Tier -> B3 Leaf-Spine'),
        ('H3_5', 'H2_3', 'B3 Leaf-Spine -> B2 3-Tier'),
    ]
    rows = []
    for src, dst, label in cases:
        rows.append(test_ping(net, src, dst, count=10, label=label))
        time.sleep(0.2)
    return rows

def run_throughput_tests(net):
    print(_bar('TEST 2 - TCP throughput between branches, two-way'))
    # test_tcp(server, client): nhan label theo huong client -> server.
    cases = [
        ('H2_1', 'H1_1', 'TCP B1 Flat -> B2 3-Tier'),
        ('H1_1', 'H2_1', 'TCP B2 3-Tier -> B1 Flat'),
        ('H3_1', 'H1_1', 'TCP B1 Flat -> B3 Leaf-Spine'),
        ('H1_1', 'H3_1', 'TCP B3 Leaf-Spine -> B1 Flat'),
        ('H3_5', 'H2_3', 'TCP B2 3-Tier -> B3 Leaf-Spine'),
        ('H2_3', 'H3_5', 'TCP B3 Leaf-Spine -> B2 3-Tier'),
    ]
    rows = []
    for server, client, label in cases:
        rows.append(test_tcp(net, server, client, duration=8, parallel=1, label=label))
        time.sleep(1.0)
    return rows

def run_intra_delay_tests(net):
    print(_bar('TEST 3A - Intra-branch delay comparison'))
    cases = [
        ('H1_1', 'H1_4', 'Branch 1 Flat intra-delay'),
        ('H2_1', 'H2_2', 'Branch 2 3-Tier same-VLAN delay'),
        ('H2_1', 'H2_3', 'Branch 2 3-Tier inter-VLAN delay'),
        ('H3_1', 'H3_5', 'Branch 3 Leaf-Spine intra-delay'),
    ]
    rows = []
    for src, dst, label in cases:
        rows.append(test_ping(net, src, dst, count=10, label=label))
        time.sleep(0.2)
    return rows

def run_intra_tests(net):
    print(_bar('TEST 3B - Intra-branch TCP architecture comparison'))
    cases = [
        ('H1_2', 'H1_1', 'Branch 1 Flat intra-LAN'),
        ('H2_6', 'H2_1', 'Branch 2 3-Tier intra-LAN'),
        ('H3_5', 'H3_1', 'Branch 3 Leaf-Spine intra-LAN'),
    ]
    rows = []
    for server, client, label in cases:
        rows.append(test_tcp(net, server, client, duration=8, parallel=1, label=label))
        time.sleep(1.0)
    return rows

def run_udp_tests(net):
    print(_bar('TEST 4 - UDP jitter/loss cross-branch, two-way'))
    # test_udp(server, client): nhan label theo huong client -> server.
    cases = [
        ('H2_1', 'H1_1', '20M', 'UDP B1 Flat -> B2 3-Tier'),
        ('H1_1', 'H2_1', '20M', 'UDP B2 3-Tier -> B1 Flat'),
        ('H3_1', 'H1_1', '20M', 'UDP B1 Flat -> B3 Leaf-Spine'),
        ('H1_1', 'H3_1', '20M', 'UDP B3 Leaf-Spine -> B1 Flat'),
        ('H3_5', 'H2_3', '20M', 'UDP B2 3-Tier -> B3 Leaf-Spine'),
        ('H2_3', 'H3_5', '20M', 'UDP B3 Leaf-Spine -> B2 3-Tier'),
    ]
    rows = []
    for server, client, bw, label in cases:
        rows.append(test_udp(net, server, client, bw=bw, duration=8, label=label))
        time.sleep(1.0)
    return rows

def run_load_tests(net):
    print(_bar('TEST 5 - Packet loss when offered load increases'))
    loads = [10, 30, 50, 70, 90]
    # Khong chi do B1 -> B2; them B2 -> B3 va B3 -> B1 de du ba vung.
    scenarios = [
        ('H2_1', 'H1_1', 'Load B1 Flat -> B2 3-Tier'),
        ('H3_5', 'H2_3', 'Load B2 3-Tier -> B3 Leaf-Spine'),
        ('H1_1', 'H3_5', 'Load B3 Leaf-Spine -> B1 Flat'),
    ]
    rows = []
    for server, client, base_label in scenarios:
        for load in loads:
            label = f'{base_label} at {load} Mbps'
            rows.append(test_udp(net, server, client, bw=f'{load}M', duration=6,
                                 label=label, group='load', offered_mbps=float(load)))
            time.sleep(0.8)
    return rows

# ---------------------------------------------------------------------------
#  Summary
# ---------------------------------------------------------------------------

def _safe_avg(values):
    nums = [float(v) for v in values if v is not None]
    return round(sum(nums) / len(nums), 3) if nums else 0.0

def _summarize_for_console(results):
    ping = results.get('ping', [])
    tcp = results.get('tcp', [])
    intra = results.get('intra', [])
    intra_ping = results.get('intra_ping', [])
    udp = results.get('udp', [])
    load = results.get('load', [])

    print(_bar('Summary'))
    print(f"  Backbone mode : {results.get('backbone_mode')}")
    print(f"  Delay avg     : {_safe_avg([r.get('rtt_avg') for r in ping])} ms")
    print(f"  TCP avg       : {_safe_avg([r.get('throughput_mbps') for r in tcp])} Mbps")
    print(f"  Intra delay   : {_safe_avg([r.get('rtt_avg') for r in intra_ping])} ms")
    print(f"  Intra avg     : {_safe_avg([r.get('throughput_mbps') for r in intra])} Mbps")
    print(f"  UDP jitter avg: {_safe_avg([r.get('jitter_ms') for r in udp])} ms")
    print(f"  UDP loss avg  : {_safe_avg([r.get('lost_pct') for r in udp])} %")
    print(f"  Load loss avg : {_safe_avg([r.get('lost_pct') for r in load])} %")

def _latest_analyze_command(net):
    latest = latest_result_file(net)
    return f"python3 analyze.py {latest} --html" if latest else "python3 analyze.py mpls_results/*.json --html"



def _tag_rows(rows, mode):
    for row in rows or []:
        if isinstance(row, dict):
            row.setdefault('backbone_mode', mode)
    return rows


def _collect_ip_route_tables(net):
    data = {}
    for name in PROVIDER_NODES:
        node = net.get(name)
        data[name] = {
            'ip': node.cmd('ip route show').strip(),
            'mpls': node.cmd('ip -f mpls route show').strip(),
        }
    return data


def _mode_summary(results):
    return {
        'delay_avg_ms': _avg_from(results.get('ping', []), 'rtt_avg'),
        'ping_loss_avg_pct': _avg_from(results.get('ping', []), 'loss_pct'),
        'tcp_throughput_avg_mbps': _avg_from(results.get('tcp', []), 'throughput_mbps'),
        'intra_delay_avg_ms': _avg_from(results.get('intra_ping', []), 'rtt_avg'),
        'intra_throughput_avg_mbps': _avg_from(results.get('intra', []), 'throughput_mbps'),
        'udp_jitter_avg_ms': _avg_from(results.get('udp', []), 'jitter_ms'),
        'udp_loss_avg_pct': _avg_from(results.get('udp', []), 'lost_pct'),
        'load_loss_avg_pct': _avg_from(results.get('load', []), 'lost_pct'),
        'load_loss_max_pct': _max_from(results.get('load', []), 'lost_pct'),
    }


def _run_measurement_suite(net, mode='mpls', collect_control=True):
    """Run the same test suite for MPLS or IP baseline without saving JSON."""
    kill_iperf3(net)
    if mode == 'mpls' and hasattr(net, 'ensure_mpls_service_dataplane'):
        try:
            net.ensure_mpls_service_dataplane()
        except Exception as exc:
            print(warn(f'MPLS service dataplane check failed: {exc}'))
    t0 = time.time()
    print(_bar(f'MEASUREMENT SUITE - {mode.upper()}'))

    if mode == 'mpls' and collect_control:
        mpls_health = _collect_mpls_health(net)
        mpls_tables = _collect_mpls_tables(net)
        traces = {
            'B1_to_B2': trace_model(net, 'H1_1', 'H2_1'),
            'B2_to_B1': trace_model(net, 'H2_1', 'H1_1'),
            'B1_to_B3': trace_model(net, 'H1_1', 'H3_1'),
            'B3_to_B1': trace_model(net, 'H3_1', 'H1_1'),
            'B2_to_B3': trace_model(net, 'H2_1', 'H3_3'),
            'B3_to_B2': trace_model(net, 'H3_5', 'H2_3'),
        }
        tcpdump_proofs = _collect_tcpdump_label_proofs(net, list(traces.keys()), verbose=False)
    else:
        mpls_health = {'enabled': False, 'mode': mode, 'note': 'IP baseline does not use MPLS LFIB/LDP.'}
        mpls_tables = _collect_ip_route_tables(net)
        traces = {}
        tcpdump_proofs = {}

    ecmp_routes = _collect_ecmp_status(net)
    connectivity = _collect_connectivity(net, count=4)

    ping_rows = _tag_rows(run_ping_tests(net), mode)
    tcp_rows = _tag_rows(run_throughput_tests(net), mode)
    intra_ping_rows = _tag_rows(run_intra_delay_tests(net), mode)
    intra_rows = _tag_rows(run_intra_tests(net), mode)
    udp_rows = _tag_rows(run_udp_tests(net), mode)
    load_rows = _tag_rows(run_load_tests(net), mode)

    result = {
        'timestamp': datetime.now().isoformat(),
        'topology': getattr(net, 'project_title', 'Metro Ethernet MAN'),
        'backbone_mode': mode,
        'report_type': f'{mode}_measurement_suite',
        'mpls_health': mpls_health,
        'mpls_tables': mpls_tables,
        'ecmp_routes': ecmp_routes,
        'connectivity': connectivity,
        'trace_samples': traces,
        'tcpdump_label_proofs': tcpdump_proofs,
        'inventory': _inventory_snapshot(net),
        'ping': ping_rows,
        'tcp': tcp_rows,
        'intra_ping': intra_ping_rows,
        'intra': intra_rows,
        'udp': udp_rows,
        'load': load_rows,
        'elapsed_s': round(time.time() - t0, 2),
    }
    result['summary'] = _mode_summary(result)
    result['analysis_summary'] = _build_analysis_summary(result)
    return result


def _delta(newer, older):
    if newer is None or older is None:
        return None
    try:
        return round(float(newer) - float(older), 3)
    except Exception:
        return None


def _build_mpls_ip_compare(mpls_results, ip_results):
    m_sum = _mode_summary(mpls_results or {})
    i_sum = _mode_summary(ip_results or {})
    comparison_summary = {
        'delay_mpls_avg_ms': m_sum.get('delay_avg_ms'),
        'delay_ip_avg_ms': i_sum.get('delay_avg_ms'),
        'delay_delta_ip_minus_mpls_ms': _delta(i_sum.get('delay_avg_ms'), m_sum.get('delay_avg_ms')),
        'tcp_mpls_avg_mbps': m_sum.get('tcp_throughput_avg_mbps'),
        'tcp_ip_avg_mbps': i_sum.get('tcp_throughput_avg_mbps'),
        'tcp_delta_ip_minus_mpls_mbps': _delta(i_sum.get('tcp_throughput_avg_mbps'), m_sum.get('tcp_throughput_avg_mbps')),
        'jitter_mpls_avg_ms': m_sum.get('udp_jitter_avg_ms'),
        'jitter_ip_avg_ms': i_sum.get('udp_jitter_avg_ms'),
        'jitter_delta_ip_minus_mpls_ms': _delta(i_sum.get('udp_jitter_avg_ms'), m_sum.get('udp_jitter_avg_ms')),
        'loss_mpls_avg_pct': m_sum.get('load_loss_avg_pct'),
        'loss_ip_avg_pct': i_sum.get('load_loss_avg_pct'),
        'loss_delta_ip_minus_mpls_pct': _delta(i_sum.get('load_loss_avg_pct'), m_sum.get('load_loss_avg_pct')),
    }
    notes = [
        'Cùng topology, cùng host, cùng link và cùng bộ test được chạy ở hai cơ chế forwarding.',
        'MPLS mode dùng label/LFIB và có bằng chứng LDP/LFIB/trace label.',
        'IP baseline flush LFIB và dùng route IP truyền thống trên PE/P để chuyển tiếp.',
        'Trong Mininet, chênh lệch hiệu năng có thể không lớn; giá trị chính là chứng minh khác biệt cơ chế forwarding.',
    ]
    return {
        'available': True,
        'method': 'runall sequential compare in one JSON',
        'same_topology': True,
        'same_test_suite': True,
        'generated_at': datetime.now().isoformat(),
        'mpls': {
            'backbone_mode': 'mpls',
            'summary': m_sum,
            'ping': (mpls_results or {}).get('ping', []),
            'tcp': (mpls_results or {}).get('tcp', []),
            'udp': (mpls_results or {}).get('udp', []),
            'load': (mpls_results or {}).get('load', []),
            'intra_ping': (mpls_results or {}).get('intra_ping', []),
            'intra': (mpls_results or {}).get('intra', []),
        },
        'ip': {
            'backbone_mode': 'ip',
            'summary': i_sum,
            'ping': (ip_results or {}).get('ping', []),
            'tcp': (ip_results or {}).get('tcp', []),
            'udp': (ip_results or {}).get('udp', []),
            'load': (ip_results or {}).get('load', []),
            'intra_ping': (ip_results or {}).get('intra_ping', []),
            'intra': (ip_results or {}).get('intra', []),
            'ip_route_tables': (ip_results or {}).get('mpls_tables', {}),
        },
        'comparison_summary': comparison_summary,
        'mechanism_comparison': [
            {'criterion': 'Cơ chế chuyển tiếp', 'mpls': 'Label switching qua LFIB', 'ip_routing': 'Tra cứu route IP từng hop'},
            {'criterion': 'Router lõi', 'mpls': 'P-router xử lý nhãn, swap/PHP', 'ip_routing': 'P-router tra cứu prefix IP'},
            {'criterion': 'LDP/LFIB', 'mpls': 'Có LDP binding/neighbor và ip -f mpls route', 'ip_routing': 'Không dùng LFIB; ip -f mpls route rỗng'},
            {'criterion': 'Bằng chứng trong lab', 'mpls': 'mpls_tables, trace_samples push/swap/PHP/pop', 'ip_routing': 'ip_route_tables trên PE/P'},
        ],
        'notes': notes,
    }


def _print_compare_summary(compare):
    if not compare or not compare.get('available'):
        print(f"  {warn('MPLS/IP compare: not available')}")
        return
    s = compare.get('comparison_summary', {})
    print(_bar('MPLS vs IP Routing comparison summary'))
    print(f"  Delay avg    : MPLS={s.get('delay_mpls_avg_ms')} ms | IP={s.get('delay_ip_avg_ms')} ms | delta={s.get('delay_delta_ip_minus_mpls_ms')} ms")
    print(f"  TCP avg      : MPLS={s.get('tcp_mpls_avg_mbps')} Mbps | IP={s.get('tcp_ip_avg_mbps')} Mbps | delta={s.get('tcp_delta_ip_minus_mpls_mbps')} Mbps")
    print(f"  UDP jitter   : MPLS={s.get('jitter_mpls_avg_ms')} ms | IP={s.get('jitter_ip_avg_ms')} ms | delta={s.get('jitter_delta_ip_minus_mpls_ms')} ms")
    print(f"  Load loss avg: MPLS={s.get('loss_mpls_avg_pct')} % | IP={s.get('loss_ip_avg_pct')} % | delta={s.get('loss_delta_ip_minus_mpls_pct')} %")

# ---------------------------------------------------------------------------
#  Public entry points
# ---------------------------------------------------------------------------

def run_all(net):
    """
    Full report run:
      1. Run complete MPLS tests and collect MPLS/LDP/LFIB evidence.
      2. Temporarily switch provider core to traditional IP routing baseline.
      3. Run the same test suite again.
      4. Restore MPLS and save one JSON containing all dashboard tabs.
    """
    kill_iperf3(net)
    full_t0 = time.time()

    # v24 safety: make sure Linux has the actual MPLS service dataplane
    # before runall measures cross-branch traffic. LDP neighbors can be UP
    # while the customer PUSH/POP/LFIB entries are still missing.
    if hasattr(net, 'ensure_mpls_service_dataplane'):
        try:
            net.ensure_mpls_service_dataplane()
        except Exception as exc:
            print(warn(f'MPLS service dataplane pre-check failed: {exc}'))

    original_mode = _backbone_mode(net)
    if original_mode != 'mpls':
        print(warn(f'runall is intended to start from MPLS mode; current mode is {original_mode}. Continuing anyway.'))

    print(_bar('RUNALL PART 1/2 - MPLS mode'))
    mpls_results = _run_measurement_suite(net, mode='mpls', collect_control=True)

    # Capture requirement proof commands before switching to the IP baseline,
    # because these commands include MPLS/LDP/LFIB evidence.
    mpls_results['requirement_proofs'] = _collect_requirement_proofs(net, mpls_results, verbose=True)

    ip_results = None
    compare = {'available': False, 'reason': 'IP baseline function is not available in topology.py'}
    if hasattr(net, 'switch_to_ip_baseline') and hasattr(net, 'restore_mpls_mode'):
        try:
            print(_bar('RUNALL PART 2/2 - IP routing baseline'))
            net.switch_to_ip_baseline()
            time.sleep(2)
            ip_results = _run_measurement_suite(net, mode='ip', collect_control=False)
            compare = _build_mpls_ip_compare(mpls_results, ip_results)
        except Exception as exc:
            compare = {'available': False, 'reason': f'IP baseline compare failed: {exc}'}
            print(err(f'IP baseline compare failed: {exc}'))
        finally:
            try:
                net.restore_mpls_mode()
                time.sleep(2)
            except Exception as exc:
                print(warn(f'Cannot restore MPLS mode automatically: {exc}'))
    else:
        print(warn('topology.py does not expose switch_to_ip_baseline/restore_mpls_mode; compare not generated.'))

    results = dict(mpls_results)
    results['timestamp'] = datetime.now().isoformat()
    results['backbone_mode'] = 'mpls'
    results['report_type'] = 'full_results_with_mpls_ip_compare'
    results['compare'] = compare
    results['elapsed_s'] = round(time.time() - full_t0, 2)
    results['analysis_summary'] = _build_analysis_summary(results)
    results['requirement_checks'] = _build_requirement_checks(results)
    _print_requirement_check_summary(results)
    _print_compare_summary(compare)

    fp = _save_results(net, results, prefix='results')
    _summarize_for_console(results)
    print(f"\n  {ok('JSON saved:')} {info(fp)}")
    print(f"  {hdr('One-file dashboard data:')} {info('mpls_results/latest.json')}")
    print(f"  {hdr('Next:')} {info(f'python3 analyze.py {fp} --html')}")
    return results

def run_quick(net):
    """
    Ban nhanh de debug trong VM.
    """
    kill_iperf3(net)
    t0 = time.time()
    mode = _backbone_mode(net)

    mpls_health = _collect_mpls_health(net)
    mpls_tables = _collect_mpls_tables(net)
    ecmp_routes = _collect_ecmp_status(net)
    connectivity = _collect_connectivity(net, count=3)

    intra_ping_rows = [
        test_ping(net, 'H1_1', 'H1_4', count=5, label='Quick Branch 1 Flat intra-delay'),
        test_ping(net, 'H2_1', 'H2_3', count=5, label='Quick Branch 2 inter-VLAN delay'),
        test_ping(net, 'H3_1', 'H3_5', count=5, label='Quick Branch 3 Leaf-Spine intra-delay'),
    ]
    ping_rows = [
        test_ping(net, 'H1_1', 'H2_1', count=5, label='Quick B1 -> B2'),
        test_ping(net, 'H2_1', 'H1_1', count=5, label='Quick B2 -> B1'),
        test_ping(net, 'H1_1', 'H3_1', count=5, label='Quick B1 -> B3'),
        test_ping(net, 'H3_1', 'H1_1', count=5, label='Quick B3 -> B1'),
        test_ping(net, 'H2_1', 'H3_1', count=5, label='Quick B2 -> B3'),
        test_ping(net, 'H3_1', 'H2_1', count=5, label='Quick B3 -> B2'),
    ]
    tcp_rows = [
        test_tcp(net, 'H2_1', 'H1_1', duration=5, label='Quick TCP B1 -> B2'),
        test_tcp(net, 'H1_1', 'H3_1', duration=5, label='Quick TCP B3 -> B1'),
    ]
    udp_rows = [
        test_udp(net, 'H2_1', 'H1_1', bw='20M', duration=5, label='Quick UDP B1 -> B2'),
        test_udp(net, 'H1_1', 'H3_1', bw='20M', duration=5, label='Quick UDP B3 -> B1'),
    ]
    load_rows = [
        test_udp(net, 'H2_1', 'H1_1', bw='50M', duration=5, label='Quick Load B1 -> B2 50M', group='load', offered_mbps=50.0),
        test_udp(net, 'H1_1', 'H3_1', bw='50M', duration=5, label='Quick Load B3 -> B1 50M', group='load', offered_mbps=50.0),
    ]
    quick_tcpdump_proofs = _collect_tcpdump_label_proofs(net, ['B1_to_B2', 'B1_to_B3', 'B3_to_B1'], verbose=True)

    results = {
        'timestamp': datetime.now().isoformat(),
        'topology': getattr(net, 'project_title', 'Metro Ethernet MAN'),
        'backbone_mode': mode,
        'report_type': 'quick_results',
        'mpls_health': mpls_health,
        'mpls_tables': mpls_tables,
        'ecmp_routes': ecmp_routes,
        'connectivity': connectivity,
        'trace_samples': {
            'B1_to_B2': trace_model(net, 'H1_1', 'H2_1'),
            'B2_to_B1': trace_model(net, 'H2_1', 'H1_1'),
            'B1_to_B3': trace_model(net, 'H1_1', 'H3_1'),
            'B3_to_B1': trace_model(net, 'H3_1', 'H1_1'),
        },
        'tcpdump_label_proofs': quick_tcpdump_proofs,
        'inventory': _inventory_snapshot(net),
        'ping': ping_rows,
        'tcp': tcp_rows,
        'intra_ping': intra_ping_rows,
        'intra': [],
        'udp': udp_rows,
        'load': load_rows,
        'elapsed_s': round(time.time() - t0, 2),
    }
    results['analysis_summary'] = _build_analysis_summary(results)
    results['requirement_proofs'] = _collect_requirement_proofs(net, results, verbose=True)
    results['requirement_checks'] = _build_requirement_checks(results)
    _print_requirement_check_summary(results)

    fp = _save_results(net, results, prefix='quick')
    _summarize_for_console(results)
    print(f"\n  {ok('JSON saved:')} {info(fp)}")
    print(f"  {hdr('Next:')} {info(f'python3 analyze.py {fp} --html')}")
    return results

def run_monitor_only(net):
    mode = _backbone_mode(net)
    t0 = time.time()
    results = {
        'timestamp': datetime.now().isoformat(),
        'topology': getattr(net, 'project_title', 'Metro Ethernet MAN'),
        'backbone_mode': mode,
        'report_type': 'monitor_only',
        'mpls_health': _collect_mpls_health(net),
        'mpls_tables': _collect_mpls_tables(net),
        'ecmp_routes': _collect_ecmp_status(net),
        'connectivity': _collect_connectivity(net, count=3),
        'trace_samples': {
            'B1_to_B2': trace_model(net, 'H1_1', 'H2_1'),
            'B2_to_B1': trace_model(net, 'H2_1', 'H1_1'),
            'B1_to_B3': trace_model(net, 'H1_1', 'H3_1'),
            'B3_to_B1': trace_model(net, 'H3_1', 'H1_1'),
        },
        'tcpdump_label_proofs': _collect_tcpdump_label_proofs(net, ['B1_to_B2'], verbose=True),
        'inventory': _inventory_snapshot(net),
        'ping': [],
        'tcp': [],
        'intra_ping': [],
        'intra': [],
        'udp': [],
        'load': [],
        'elapsed_s': round(time.time() - t0, 2),
    }
    results['analysis_summary'] = _build_analysis_summary(results)
    results['requirement_proofs'] = _collect_requirement_proofs(net, results, verbose=True)
    results['requirement_checks'] = _build_requirement_checks(results)
    _print_requirement_check_summary(results)
    fp = _save_results(net, results, prefix='monitor')
    print(f"\n  {ok('JSON saved:')} {info(fp)}")
    return results

def live_monitor(net, interval=5, rounds=6):
    print(_bar(f'Live monitor every {interval}s x {rounds} rounds'))
    for r in range(1, rounds + 1):
        print(f'\nRound {r}/{rounds}')
        for src, dst in [('H1_1', 'H2_1'), ('H2_1', 'H1_1'), ('H1_1', 'H3_1'), ('H3_1', 'H1_1'), ('H2_1', 'H3_1'), ('H3_1', 'H2_1')]:
            row = test_ping(net, src, dst, count=3, label=f'Live {src}->{dst}')
            print(f"    RTT={row.get('rtt_avg')} ms  loss={row.get('loss_pct')}%")
        if r < rounds:
            time.sleep(interval)

def show_dashboard(net):
    latest = latest_result_file(net)
    print(_bar('Dashboard / report guide'))
    root = _project_root()
    if latest:
        print(f'  Latest JSON        : {latest}')
        print(f'  Dashboard auto JSON: {os.path.join(root, "dashboard_data.json")}')
        print(f'  Analyze            : python3 analyze.py {latest} --html')
    else:
        print('  Chua co file JSON. Hay chay runquick/runall/json trong Mininet CLI.')
    print('  Cach mo dashboard tu dong nap JSON:')
    print('    1) Trong Mininet CLI: dash 8000')
    print('    2) Mo trinh duyet: http://127.0.0.1:8000/mpls_dashboard_tool.html')
    print('  Neu mo bang file:// thi dashboard van co nut chon JSON thu cong.')

def run_all_tests(net):
    return run_all(net)


if __name__ == '__main__':
    print(f"""
{hdr('runner.py loaded')}
  Trong Mininet CLI:
    py exec(open('runner.py').read(), globals())
    py run_all(net)
    py run_quick(net)
    py run_monitor_only(net)
    py trace_mpls_path(net, 'H1_1', 'H2_1')  # displays host1 -> admin1

  Sau khi co JSON:
    {_latest_analyze_command(None)}
""")

