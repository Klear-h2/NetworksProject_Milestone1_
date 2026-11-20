"""Create an operational summary and simple SVG plot from raw events.csv.
Usage: py -m gateway.analyze_logs evidence/hardware/events.csv
Milestone 1 operational evidence ONLY, not a formal Milestone 2 benchmark.
"""
import argparse
import csv
import html
import json
from pathlib import Path


def analyze(csvpath):
    path = Path(csvpath)
    rows = list(csv.DictReader(path.open(encoding='utf-8', newline='')))
    reports, started = [], {}
    duration_ms = []
    for row in rows:
        try:
            details = json.loads(row['details_json'])
        except (ValueError, KeyError):
            continue
        if row['link'] != 'GATEWAY':
            continue
        jid = details.get('job_id')
        if row['event'] == 'INSPECTION_SENT' and jid:
            started[jid] = float(row['monotonic_s'])
        if row['event'] == 'INSPECTION_COMPLETED' and jid in started:
            duration_ms.append((float(row['monotonic_s']) - started.pop(jid)) * 1000)
        if row['event'] == 'BATCH_COMPLETED':
            reports.append(details)
    if not reports:
        raise SystemExit('No BATCH_COMPLETED events found. Run and complete a batch first.')
    last = reports[-1]
    summary = {
        'batch_id': last['batch_id'], 'produced': last['produced'],
        'accepted': last['accepted'], 'rejected': last['rejected'],
        'inspection_ms_mean_observed': round(sum(duration_ms)/len(duration_ms), 3) if duration_ms else None,
        'inspection_ms_note': 'Gateway round trip from dispatch to result (includes link and scheduling time)'
    }
    out = path.parent
    (out / 'batch_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    bars = [('Accepted', int(last['accepted']), '#16a34a'), ('Rejected', int(last['rejected']), '#e11d48')]
    maximum = max(1, *(v for _, v, _ in bars))
    chunks = ['<svg xmlns="http://www.w3.org/2000/svg" width="680" height="235" viewBox="0 0 680 235">',
              '<rect width="680" height="235" rx="15" fill="#ffffff"/>',
              '<text x="25" y="34" font-family="Arial" font-size="20" font-weight="bold">Milestone 1 — Batch results (parts)</text>']
    for i, (label, value, color) in enumerate(bars):
        y = 72 + i * 72
        width = round(value / maximum * 420)
        chunks += [f'<text x="28" y="{y+18}" font-family="Arial" font-size="15">{html.escape(label)}</text>',
                   f'<rect x="130" y="{y}" width="{width}" height="28" rx="5" fill="{color}"/>',
                   f'<text x="{145 + width}" y="{y+20}" font-family="Arial" font-size="15">{value}</text>']
    chunks += ['<text x="25" y="219" font-family="Arial" font-size="11" fill="#64748b">Actual measurements depend on running this script with hardware event logs.</text>', '</svg>']
    (out / 'batch_plot.svg').write_text('\n'.join(chunks), encoding='utf-8')
    print('Saved:', out / 'batch_summary.json', 'and', out / 'batch_plot.svg')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('csvpath', nargs='?', default='evidence/hardware/events.csv')
    analyze(parser.parse_args().csvpath)
