"""Derive diagnostics from saved runs without changing raw records or using models.

Usage: python scripts/replay_assistance_diagnostics.py SAVED_RUN_DIR NEW_OUTPUT_DIR
The input directory contains profile/raw.json and profile/candidate-round-N.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.answer_quality import answer_checks
from tools.answer_contracts import render_answer
from tools.assistance_handoff import build_packet


def replay(saved, output):
    if output.exists():
        raise FileExistsError('派生输出目录已存在，请选择新目录；不覆盖历史证据。')
    profiles = ('document-comparison', 'disclosure-search', 'public-reference')
    # Validate every input before creating any output.
    runs = {p: json.loads((saved/p/'raw.json').read_text()) for p in profiles}
    originals = {str(p.relative_to(saved)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for profile in profiles for p in (saved/profile).glob('*') if p.is_file()}
    output.mkdir(parents=True)
    summary = {}
    for profile, run in runs.items():
        artifacts = {}
        for path in (saved/profile).glob('candidate-round-*.md'):
            text = path.read_text()
            artifacts[hashlib.sha256(text.encode()).hexdigest()] = text
        packet = build_packet(run, run['result'], run['evidence'], artifacts.get)
        checks = []
        for event in run['events']:
            if event['event'] != 'reflection':
                continue
            review = event['data']
            original = artifacts.get(review.get('candidate_report_ref'))
            if original is None:
                continue
            rendered, audit = render_answer(original, run['evidence'], run.get('execution_query') or run['query'])
            record = {'round': review['round'], 'historical_review_unchanged': review.get('verification'),
                      'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
                      'derived_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                      'original_checks': answer_checks(run.get('execution_query') or run['query'], original, run['evidence']),
                      'derived_checks': answer_checks(run.get('execution_query') or run['query'], rendered, run['evidence']),
                      'render_audit': audit}
            checks.append(record)
            (output/f'{profile}-round-{review["round"]}-derived.md').write_text(rendered)
        (output/f'{profile}-packet.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2))
        summary[profile] = {'rounds': checks, 'packet_available': packet is not None,
                            'historical_business_status': (run.get('result_assessment') or {}).get('product_boundary', {}).get('status'),
                            'new_business_verdict': None}
    for name, sha in originals.items():
        if hashlib.sha256((saved/name).read_bytes()).hexdigest() != sha:
            raise RuntimeError('输入证据发生变化：'+name)
    result = {'scope': '保存记录的程序派生回放；非新模型运行、非新业务通过率',
              'paid_model_calls': 0, 'original_files_unchanged': True,
              'input_sha256': originals, 'profiles': summary}
    (output/'replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('saved', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = replay(args.saved, args.output)
    print(json.dumps({'paid_model_calls': 0, 'original_files_unchanged': True,
                      'packets': {p:v['packet_available'] for p,v in result['profiles'].items()}},ensure_ascii=False))
