from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import os
import tempfile
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Tuple

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


BRANCH_LABELS = {
    'branch1': 'Branch 1 - Flat',
    'branch2': 'Branch 2 - 3-Tier',
    'branch3': 'Branch 3 - Leaf-Spine',
}

OUTPUT_BASENAMES = [
    'detailed_results.csv',
    'summary.csv',
    'throughput_cross_branch.png',
    'delay_cross_branch.png',
    'packet_loss_load.png',
    'architecture_compare.png',
    'mode_compare.png',
    'dashboard_data.json',
    'report.html',
]


def safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def avg(values: List[Any]) -> float:
    nums = [float(v) for v in values if v is not None]
    return round(mean(nums), 3) if nums else 0.0


def expand_inputs(items: List[str]) -> List[Path]:
    files: List[Path] = []
    for item in items:
        p = Path(item)
        if p.exists() and p.is_file():
            files.append(p.resolve())
            continue
        if p.exists() and p.is_dir():
            files.extend(sorted(p.glob('*.json')))
            continue
        files.extend(Path(x).resolve() for x in glob.glob(item))
    seen = set()
    uniq = []
    for f in files:
        if f.suffix.lower() != '.json':
            continue
        key = str(f)
        if key not in seen:
            seen.add(key)
            uniq.append(Path(key))
    return uniq


def load_json(path: Path) -> Dict[str, Any]:
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def img_to_b64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=180, bbox_inches='tight')
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def save_b64_png(b64: str, path: Path) -> None:
    if b64:
        path.write_bytes(base64.b64decode(b64))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _dir_is_writable_for_outputs(path: Path, basenames: List[str]) -> tuple[bool, str]:
    try:
        _ensure_dir(path)
    except Exception as e:
        return False, f'khong tao duoc thu muc ({e})'

    for name in basenames:
        target = path / name
        if target.exists() and not os.access(target, os.W_OK):
            return False, f'khong ghi de duoc file co san: {target.name}'

    probe = path / f'.probe_write_{os.getpid()}'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return True, 'ok'
    except Exception as e:
        return False, f'khong ghi thu duoc ({e})'


def choose_output_dir(preferred: Path, explicit: str | None = None) -> tuple[Path, str | None]:
    preferred = preferred.resolve()

    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        ok, reason = _dir_is_writable_for_outputs(explicit_path, OUTPUT_BASENAMES)
        if not ok:
            raise PermissionError(f'--out-dir {explicit_path} khong ghi duoc: {reason}')
        return explicit_path, None

    candidates = [
        preferred,
        (Path.cwd().resolve() / 'analysis_outputs'),
        (Path.home().resolve() / 'mpls_analysis'),
        (Path(tempfile.gettempdir()).resolve() / f'mpls_analysis_{os.getuid()}'),
    ]

    checked: List[str] = []
    for candidate in candidates:
        ok, reason = _dir_is_writable_for_outputs(candidate, OUTPUT_BASENAMES)
        if ok:
            if candidate == preferred:
                return candidate, None
            return candidate, (
                f'Thu muc ket qua goc {preferred} khong ghi duoc, '
                f'tu dong chuyen output sang {candidate}.'
            )
        checked.append(f'{candidate}: {reason}')

    raise PermissionError('Khong tim duoc thu muc nao co the ghi output. Da thu: ' + ' | '.join(checked))


# ---------------------------------------------------------------------------
#  Flatten / summarize
# ---------------------------------------------------------------------------

def flatten_result(result: Dict[str, Any], source_file: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    mode = result.get('backbone_mode', 'unknown')
    timestamp = result.get('timestamp')
    topology = result.get('topology', 'Metro Ethernet MAN')

    def append_rows(section: str, items: List[Dict[str, Any]]):
        for item in items or []:
            row = {
                'source_file': source_file,
                'timestamp': timestamp,
                'topology': topology,
                'backbone_mode': mode,
                'section': section,
                'label': item.get('label', section),
                'src': item.get('src'),
                'dst': item.get('dst'),
                'src_branch': item.get('src_branch'),
                'dst_branch': item.get('dst_branch'),
                'src_architecture': item.get('src_architecture'),
                'dst_architecture': item.get('dst_architecture'),
                'scenario_group': item.get('scenario_group', section),
                'throughput_mbps': safe_float(item.get('throughput_mbps')),
                'rtt_avg': safe_float(item.get('rtt_avg')),
                'loss_pct': safe_float(item.get('loss_pct')),
                'jitter_ms': safe_float(item.get('jitter_ms')),
                'udp_loss_pct': safe_float(item.get('lost_pct')),
                'offered_load_mbps': safe_float(item.get('offered_load_mbps')),
                'parallel': item.get('parallel'),
            }
            rows.append(row)

    append_rows('ping', result.get('ping', []))
    append_rows('tcp', result.get('tcp', []))
    append_rows('intra', result.get('intra', []))
    append_rows('udp', result.get('udp', []))
    append_rows('load', result.get('load', []))
    return rows



def summarize_one(result: Dict[str, Any]) -> Dict[str, Any]:
    ping = result.get('ping', [])
    tcp = result.get('tcp', [])
    intra = result.get('intra', [])
    udp = result.get('udp', [])
    load = result.get('load', [])

    overall = {
        'backbone_mode': result.get('backbone_mode', 'unknown'),
        'delay_avg_ms': avg([safe_float(r.get('rtt_avg')) for r in ping]),
        'tcp_avg_mbps': avg([safe_float(r.get('throughput_mbps')) for r in tcp]),
        'intra_avg_mbps': avg([safe_float(r.get('throughput_mbps')) for r in intra]),
        'udp_jitter_avg_ms': avg([safe_float(r.get('jitter_ms')) for r in udp]),
        'udp_loss_avg_pct': avg([safe_float(r.get('lost_pct')) for r in udp]),
        'load_loss_avg_pct': avg([safe_float(r.get('lost_pct')) for r in load]),
    }

    branches = []
    for row in intra:
        b = row.get('src_branch') or row.get('dst_branch')
        if not b:
            continue
        branches.append({
            'branch': b,
            'branch_name': BRANCH_LABELS.get(b, b),
            'architecture': row.get('src_architecture') or row.get('dst_architecture'),
            'throughput_mbps': safe_float(row.get('throughput_mbps')) or 0.0,
            'label': row.get('label', ''),
        })

    delay_by_label = {r.get('label', ''): safe_float(r.get('rtt_avg')) for r in ping}
    loss_by_label = {r.get('label', ''): safe_float(r.get('loss_pct')) for r in ping}
    pairs = []
    for row in tcp:
        label = row.get('label', '')
        pairs.append({
            'label': label,
            'src_branch': row.get('src_branch'),
            'dst_branch': row.get('dst_branch'),
            'src_architecture': row.get('src_architecture'),
            'dst_architecture': row.get('dst_architecture'),
            'throughput_mbps': safe_float(row.get('throughput_mbps')) or 0.0,
            'delay_ms': delay_by_label.get(label),
            'packet_loss_pct': loss_by_label.get(label),
        })

    load_curve = []
    for row in sorted(load, key=lambda r: safe_float(r.get('offered_load_mbps')) or 0):
        load_curve.append({
            'offered_load_mbps': safe_float(row.get('offered_load_mbps')) or 0.0,
            'throughput_mbps': safe_float(row.get('throughput_mbps')) or 0.0,
            'packet_loss_pct': safe_float(row.get('lost_pct')) or 0.0,
            'jitter_ms': safe_float(row.get('jitter_ms')) or 0.0,
            'label': row.get('label', ''),
        })

    return {
        'overall': overall,
        'branches': branches,
        'pairs': pairs,
        'load_curve': load_curve,
    }



def latest_results_by_mode(results_with_source: List[Tuple[Path, Dict[str, Any]]]) -> Dict[str, Tuple[Path, Dict[str, Any]]]:
    out: Dict[str, Tuple[Path, Dict[str, Any]]] = {}
    for path, result in sorted(results_with_source, key=lambda x: str(x[0])):
        out[result.get('backbone_mode', 'unknown')] = (path, result)
    return out


# ---------------------------------------------------------------------------
#  Charts
# ---------------------------------------------------------------------------

def chart_cross_throughput(summary: Dict[str, Any]) -> str:
    pairs = summary['pairs']
    labels = [p['label'] for p in pairs] or ['No data']
    values = [p['throughput_mbps'] for p in pairs] or [0.0]
    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    bars = ax.bar(labels, values)
    ax.set_title('Throughput giua cac chi nhanh (TCP)')
    ax.set_ylabel('Mbps')
    ax.tick_params(axis='x', rotation=18)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, val + max(values + [1])*0.01, f'{val:.2f}', ha='center', va='bottom', fontsize=8)
    return img_to_b64(fig)


def chart_cross_delay(result: Dict[str, Any]) -> str:
    ping = result.get('ping', [])
    labels = [r.get('label', 'ping') for r in ping] or ['No data']
    values = [safe_float(r.get('rtt_avg')) or 0.0 for r in ping] or [0.0]
    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    bars = ax.bar(labels, values)
    ax.set_title('Do tre / RTT giua cac chi nhanh')
    ax.set_ylabel('ms')
    ax.tick_params(axis='x', rotation=18)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, val + max(values + [1])*0.01, f'{val:.2f}', ha='center', va='bottom', fontsize=8)
    return img_to_b64(fig)


def chart_load_loss(summary: Dict[str, Any]) -> str:
    loads = summary['load_curve']
    x = [p['offered_load_mbps'] for p in loads] or [0.0]
    y = [p['packet_loss_pct'] for p in loads] or [0.0]
    fig, ax = plt.subplots(figsize=(9.5, 4.5))
    ax.plot(x, y, marker='o')
    ax.set_title('Packet loss khi tai UDP tang')
    ax.set_xlabel('Offered load (Mbps)')
    ax.set_ylabel('Packet loss (%)')
    ax.grid(alpha=0.3, linestyle='--')
    for xi, yi in zip(x, y):
        ax.text(xi, yi + max(y + [1])*0.03, f'{yi:.2f}', ha='center', va='bottom', fontsize=8)
    return img_to_b64(fig)


def chart_architecture_compare(summary: Dict[str, Any]) -> str:
    branches = summary['branches']
    labels = [b['branch_name'] for b in branches] or ['No data']
    values = [b['throughput_mbps'] for b in branches] or [0.0]
    fig, ax = plt.subplots(figsize=(9.5, 4.5))
    bars = ax.bar(labels, values)
    ax.set_title('So sanh throughput noi bo theo kien truc LAN')
    ax.set_ylabel('Mbps')
    ax.tick_params(axis='x', rotation=12)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, val + max(values + [1])*0.01, f'{val:.2f}', ha='center', va='bottom', fontsize=8)
    return img_to_b64(fig)


def chart_mode_compare(by_mode: Dict[str, Tuple[Path, Dict[str, Any]]]) -> str:
    if len(by_mode) < 2:
        return ''
    labels = []
    tp = []
    delay = []
    for mode, (_, result) in by_mode.items():
        s = summarize_one(result)
        labels.append(mode.upper())
        tp.append(s['overall']['tcp_avg_mbps'])
        delay.append(s['overall']['delay_avg_ms'])

    fig, axes = plt.subplots(2, 1, figsize=(7.5, 7.5), constrained_layout=True)
    bars = axes[0].bar(labels, tp)
    axes[0].set_title('So sanh backbone mode - Throughput trung binh')
    axes[0].set_ylabel('Mbps')
    axes[0].grid(axis='y', alpha=0.3, linestyle='--')
    for bar, val in zip(bars, tp):
        axes[0].text(bar.get_x() + bar.get_width()/2, val + max(tp + [1])*0.01, f'{val:.2f}', ha='center', va='bottom', fontsize=8)

    bars = axes[1].bar(labels, delay)
    axes[1].set_title('So sanh backbone mode - Delay trung binh')
    axes[1].set_ylabel('ms')
    axes[1].grid(axis='y', alpha=0.3, linestyle='--')
    for bar, val in zip(bars, delay):
        axes[1].text(bar.get_x() + bar.get_width()/2, val + max(delay + [1])*0.01, f'{val:.2f}', ha='center', va='bottom', fontsize=8)

    return img_to_b64(fig)


# ---------------------------------------------------------------------------
#  Conclusions / outputs
# ---------------------------------------------------------------------------

def generate_conclusions(summary: Dict[str, Any], by_mode: Dict[str, Tuple[Path, Dict[str, Any]]]) -> List[str]:
    conclusions: List[str] = []
    branches = summary['branches']
    pairs = summary['pairs']
    load_curve = summary['load_curve']

    if branches:
        best = max(branches, key=lambda x: x['throughput_mbps'])
        conclusions.append(
            f'Kien truc LAN noi bo co throughput cao nhat trong bo test intra-LAN la {best["branch_name"]} '
            f'({best["architecture"]}) voi {best["throughput_mbps"]:.2f} Mbps.'
        )

    if pairs:
        best_pair = max(pairs, key=lambda x: x['throughput_mbps'])
        conclusions.append(
            f'Ket noi lien chi nhanh co throughput cao nhat la {best_pair["label"]} dat {best_pair["throughput_mbps"]:.2f} Mbps.'
        )
        delay_candidates = [p for p in pairs if p.get('delay_ms') is not None]
        if delay_candidates:
            slow_pair = max(delay_candidates, key=lambda x: x.get('delay_ms') or 0)
            conclusions.append(
                f'Duong co RTT lon nhat trong bo test ping la {slow_pair["label"]} voi {safe_float(slow_pair.get("delay_ms")):.2f} ms.'
            )

    if load_curve:
        first = load_curve[0]['packet_loss_pct']
        last = load_curve[-1]['packet_loss_pct']
        trend = 'tang' if last > first + 0.2 else 'gan nhu on dinh'
        conclusions.append(
            f'Packet loss theo tai UDP {trend}: tu {first:.2f}% o muc tai {load_curve[0]["offered_load_mbps"]:.0f} Mbps '
            f'len {last:.2f}% o muc tai {load_curve[-1]["offered_load_mbps"]:.0f} Mbps.'
        )

    if len(by_mode) >= 2 and 'mpls' in by_mode and 'ip' in by_mode:
        mpls_summary = summarize_one(by_mode['mpls'][1])
        ip_summary = summarize_one(by_mode['ip'][1])
        tp_diff = mpls_summary['overall']['tcp_avg_mbps'] - ip_summary['overall']['tcp_avg_mbps']
        dl_diff = ip_summary['overall']['delay_avg_ms'] - mpls_summary['overall']['delay_avg_ms']
        conclusions.append(
            f'So sanh backbone mode: MPLS {"cao hon" if tp_diff >= 0 else "thap hon"} IP {abs(tp_diff):.2f} Mbps ve TCP trung binh '
            f'va {"giam" if dl_diff >= 0 else "tang"} {abs(dl_diff):.2f} ms ve delay trung binh.'
        )
    else:
        conclusions.append('Chua co du 2 che do MPLS va IP trong cung lan phan tich, vi vay chua the ket luan day du ve MPLS so voi IP routing.')

    return conclusions



def write_detailed_csv(rows: List[Dict[str, Any]], out_csv: Path) -> None:
    if not rows:
        out_csv.write_text('', encoding='utf-8')
        return
    with out_csv.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)



def write_summary_csv(summary: Dict[str, Any], out_csv: Path) -> None:
    rows = []
    for k, v in summary['overall'].items():
        rows.append({'group': 'overall', 'name': k, 'value': v})
    for row in summary['branches']:
        rows.append({'group': 'branch', 'name': row['branch_name'], 'value': row['throughput_mbps']})
    for row in summary['pairs']:
        rows.append({'group': 'pair', 'name': row['label'], 'value': row['throughput_mbps']})
    with out_csv.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['group', 'name', 'value'])
        writer.writeheader()
        writer.writerows(rows)



def build_dashboard_json(latest_path: Path, latest_result: Dict[str, Any], summary: Dict[str, Any],
                         by_mode: Dict[str, Tuple[Path, Dict[str, Any]]]) -> Dict[str, Any]:
    mode_compare = []
    for mode, (path, result) in by_mode.items():
        s = summarize_one(result)
        mode_compare.append({
            'mode': mode,
            'source_file': path.name,
            'tcp_avg_mbps': s['overall']['tcp_avg_mbps'],
            'delay_avg_ms': s['overall']['delay_avg_ms'],
            'udp_jitter_avg_ms': s['overall']['udp_jitter_avg_ms'],
            'udp_loss_avg_pct': s['overall']['udp_loss_avg_pct'],
        })

    return {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'source_file': latest_path.name,
            'topology': latest_result.get('topology', 'Metro Ethernet MAN'),
            'backbone_mode': latest_result.get('backbone_mode', 'unknown'),
            'elapsed_s': latest_result.get('elapsed_s'),
        },
        'summary': {
            'overall': summary['overall'],
            'branches': summary['branches'],
            'pairs': summary['pairs'],
            'load_curve': summary['load_curve'],
            'mode_compare': mode_compare,
        },
        'tests': {
            'ping': latest_result.get('ping', []),
            'tcp': latest_result.get('tcp', []),
            'intra': latest_result.get('intra', []),
            'udp': latest_result.get('udp', []),
            'load': latest_result.get('load', []),
        },
        'mpls_health': latest_result.get('mpls_health', {}),
        'ecmp_routes': latest_result.get('ecmp_routes', {}),
        'mpls_tables': latest_result.get('mpls_tables', {}),
        'trace_samples': latest_result.get('trace_samples', {}),
    }



def build_table(headers: List[str], rows: List[List[Any]]) -> str:
    thead = ''.join(f'<th>{escape(str(h))}</th>' for h in headers)
    body = ''
    for row in rows:
        body += '<tr>' + ''.join(f'<td>{escape("" if v is None else str(v))}</td>' for v in row) + '</tr>'
    return f'<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>'



def build_html(latest_path: Path, latest_result: Dict[str, Any], summary: Dict[str, Any],
               by_mode: Dict[str, Tuple[Path, Dict[str, Any]]], images: Dict[str, str], conclusions: List[str]) -> str:
    overall = summary['overall']
    branch_rows = [[b['branch_name'], b['architecture'], f'{b["throughput_mbps"]:.2f}', b['label']] for b in summary['branches']]
    pair_rows = [[p['label'], p['src_architecture'], p['dst_architecture'], f'{p["throughput_mbps"]:.2f}',
                  '' if p.get('delay_ms') is None else f'{safe_float(p.get("delay_ms")):.2f}',
                  '' if p.get('packet_loss_pct') is None else f'{safe_float(p.get("packet_loss_pct")):.2f}']
                 for p in summary['pairs']]
    load_rows = [[f'{x["offered_load_mbps"]:.0f}', f'{x["throughput_mbps"]:.2f}', f'{x["packet_loss_pct"]:.2f}', f'{x["jitter_ms"]:.2f}']
                 for x in summary['load_curve']]

    mode_rows = []
    for mode, (path, result) in by_mode.items():
        s = summarize_one(result)
        mode_rows.append([
            mode.upper(), path.name, f'{s["overall"]["tcp_avg_mbps"]:.2f}', f'{s["overall"]["delay_avg_ms"]:.2f}',
            f'{s["overall"]["udp_jitter_avg_ms"]:.2f}', f'{s["overall"]["udp_loss_avg_pct"]:.2f}'
        ])

    conclusion_html = ''.join(f'<li>{escape(c)}</li>' for c in conclusions)
    raw_json = escape(json.dumps(latest_result, indent=2, ensure_ascii=False)[:12000])

    return f'''<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>Metro Ethernet MPLS - Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font-family:Arial,Helvetica,sans-serif;background:#f5f7fb;color:#1f2937;margin:0;padding:0}}
.wrap{{max-width:1280px;margin:0 auto;padding:24px}}
h1,h2,h3{{margin:0 0 12px 0}}
header{{background:#ffffff;border-bottom:1px solid #d7e0ea;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
header .wrap{{padding:20px 24px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
.grid-4{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #d7e0ea;border-radius:14px;padding:18px;box-shadow:0 8px 24px rgba(15,23,42,.06)}}
.metric .value{{font-size:28px;font-weight:700;margin-top:8px}}
.metric .label{{font-size:12px;color:#66758a;text-transform:uppercase;letter-spacing:.08em}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border-bottom:1px solid #e5edf5;padding:10px;text-align:left;vertical-align:top}}
th{{background:#f8fbff;font-size:12px;color:#66758a}}
img{{max-width:100%;border:1px solid #e5edf5;border-radius:10px}}
pre{{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:12px;font-size:12px;max-height:480px;overflow:auto}}
ul{{margin:0;padding-left:20px;line-height:1.7}}
.small{{font-size:13px;color:#66758a}}
@media (max-width: 980px) {{ .grid, .grid-4 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>Metro Ethernet MAN - Performance Report</h1>
    <div class="small">
      Nguon du lieu: <b>{escape(latest_path.name)}</b> |
      Backbone mode: <b>{escape(str(latest_result.get('backbone_mode', 'unknown')).upper())}</b> |
      Tao luc: <b>{escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</b>
    </div>
  </div>
</header>

<div class="wrap">
  <div class="grid-4">
    <div class="card metric"><div class="label">TCP cross-branch avg</div><div class="value">{overall["tcp_avg_mbps"]:.2f} Mbps</div></div>
    <div class="card metric"><div class="label">Delay avg</div><div class="value">{overall["delay_avg_ms"]:.2f} ms</div></div>
    <div class="card metric"><div class="label">UDP jitter avg</div><div class="value">{overall["udp_jitter_avg_ms"]:.2f} ms</div></div>
    <div class="card metric"><div class="label">Load-test loss avg</div><div class="value">{overall["load_loss_avg_pct"]:.2f} %</div></div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2>Ket luan tu dong tu du lieu</h2>
    <ul>{conclusion_html}</ul>
  </div>

  <div class="grid" style="margin-top:16px">
    <div class="card">
      <h3>Throughput giua cac chi nhanh</h3>
      <img src="data:image/png;base64,{images.get("cross_tp","")}" alt="cross throughput">
    </div>
    <div class="card">
      <h3>Delay / RTT giua cac chi nhanh</h3>
      <img src="data:image/png;base64,{images.get("delay","")}" alt="delay">
    </div>
    <div class="card">
      <h3>Packet loss khi tai tang</h3>
      <img src="data:image/png;base64,{images.get("load_loss","")}" alt="load loss">
    </div>
    <div class="card">
      <h3>So sanh kien truc LAN noi bo</h3>
      <img src="data:image/png;base64,{images.get("architecture","")}" alt="architecture">
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2>Bang so sanh ket qua theo kich ban</h2>
    {build_table(['Kich ban', 'Nguon', 'Dich', 'Throughput (Mbps)', 'Delay (ms)', 'Loss (%)'], pair_rows or [['Khong co du lieu', '', '', '', '', '']])}
  </div>

  <div class="grid" style="margin-top:16px">
    <div class="card">
      <h3>So sanh theo kien truc LAN</h3>
      {build_table(['Chi nhanh', 'Kien truc', 'Throughput (Mbps)', 'Kich ban'], branch_rows or [['Khong co du lieu', '', '', '']])}
    </div>
    <div class="card">
      <h3>Load test - UDP ramp</h3>
      {build_table(['Offered load (Mbps)', 'Throughput (Mbps)', 'Packet loss (%)', 'Jitter (ms)'], load_rows or [['Khong co du lieu', '', '', '']])}
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2>So sanh backbone mode (MPLS vs IP routing)</h2>
    {('<img src="data:image/png;base64,' + images.get("mode_compare","") + '" alt="mode compare">') if images.get("mode_compare") else '<p>Chua co du 2 mode de so sanh.</p>'}
    <div style="margin-top:12px">
      {build_table(['Mode', 'File', 'TCP avg (Mbps)', 'Delay avg (ms)', 'UDP jitter avg (ms)', 'UDP loss avg (%)'], mode_rows or [['Khong co du lieu', '', '', '', '', '']])}
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2>Raw JSON (cat ngan)</h2>
    <pre>{raw_json}</pre>
  </div>
</div>
</body>
</html>'''


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description='Analyze Metro Ethernet Mininet results')
    parser.add_argument('inputs', nargs='+', help='JSON file, folder, or glob pattern')
    parser.add_argument('--html', action='store_true', help='Generate report.html')
    parser.add_argument('--out-dir', help='Thu muc luu bao cao/CSV/PNG. Mac dinh: cung thu muc voi JSON neu ghi duoc; neu khong se fallback sang analysis_outputs.')
    args = parser.parse_args()

    files = expand_inputs(args.inputs)
    if not files:
        raise SystemExit('Khong tim thay file JSON nao.')

    results_with_source = [(path, load_json(path)) for path in files]
    by_mode = latest_results_by_mode(results_with_source)

    latest_path, latest_result = results_with_source[-1]
    summary = summarize_one(latest_result)
    conclusions = generate_conclusions(summary, by_mode)

    out_dir, out_dir_warning = choose_output_dir(latest_path.parent.resolve(), args.out_dir)
    if out_dir_warning:
        print(f'[WARN] {out_dir_warning}')
        print('[WARN] Nguyen nhan thuong gap: ban da chay topology/runner bang sudo nen thu muc mpls_results bi root so huu.')
    print(f'[OK] Output directory: {out_dir}')

    all_rows: List[Dict[str, Any]] = []
    for path, result in results_with_source:
        all_rows.extend(flatten_result(result, path.name))

    detailed_csv = out_dir / 'detailed_results.csv'
    write_detailed_csv(all_rows, detailed_csv)
    print(f'[OK] CSV chi tiet: {detailed_csv}')

    summary_csv = out_dir / 'summary.csv'
    write_summary_csv(summary, summary_csv)
    print(f'[OK] CSV tong hop: {summary_csv}')

    images = {
        'cross_tp': chart_cross_throughput(summary),
        'delay': chart_cross_delay(latest_result),
        'load_loss': chart_load_loss(summary),
        'architecture': chart_architecture_compare(summary),
        'mode_compare': chart_mode_compare(by_mode),
    }

    png_map = {
        'cross_tp': out_dir / 'throughput_cross_branch.png',
        'delay': out_dir / 'delay_cross_branch.png',
        'load_loss': out_dir / 'packet_loss_load.png',
        'architecture': out_dir / 'architecture_compare.png',
        'mode_compare': out_dir / 'mode_compare.png',
    }
    for key, path in png_map.items():
        if images.get(key):
            save_b64_png(images[key], path)
            print(f'[OK] PNG: {path}')

    dashboard = build_dashboard_json(latest_path, latest_result, summary, by_mode)
    dash_path = out_dir / 'dashboard_data.json'
    dash_path.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'[OK] Dashboard JSON: {dash_path}')

    if args.html:
        html_path = out_dir / 'report.html'
        html_path.write_text(build_html(latest_path, latest_result, summary, by_mode, images, conclusions), encoding='utf-8')
        print(f'[OK] HTML report: {html_path}')

    print('\n[INFO] Goi y:')
    print(f'  - Mo file {dash_path} bang mpls_dashboard_tool.html')
    print('  - Hoac doc report.html de dua vao bao cao')
    if out_dir != latest_path.parent.resolve():
        print(f'  - JSON goc van o: {latest_path.parent.resolve()}')
        print(f'  - Bao cao moi da duoc luu sang: {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
