#!/usr/bin/env python3
"""KPI 1: Analyze PR contributions by Red Hat employees.

Reads raw PR JSON from workdir, matches against employee roster,
verifies closed-only PRs for non-standard workflows, and outputs
summary statistics plus kpi1-metadata.json.
"""
import argparse, json, os, subprocess

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--owner', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--workdir', required=True)
    p.add_argument('--roster', required=True)
    p.add_argument('--cutoff', required=True)
    args = p.parse_args()

    roster = json.load(open(args.roster))
    gh_users = {e['github_username'].lower(): e
                for e in roster['employees'] if e.get('github_username')}

    prs = json.load(open(os.path.join(args.workdir, 'raw-prs.json')))

    merged_path = os.path.join(args.workdir, 'raw-merged-prs.json')
    is_nonstandard = os.path.exists(merged_path)
    if is_nonstandard:
        merged_prs = json.load(open(merged_path))
        seen = {pr['number'] for pr in prs}
        prs.extend([pr for pr in merged_prs if pr['number'] not in seen])

    prs = [pr for pr in prs
           if (pr.get('mergedAt') or pr.get('closedAt') or '') >= args.cutoff]

    bot_names = {'pytorchmergebot','pytorchupdatebot','facebook-github-bot',
                 'github-actions','dependabot','renovate','mergify'}
    prs = [pr for pr in prs if not (
        pr.get('author',{}).get('login','').endswith('[bot]') or
        pr.get('author',{}).get('login','').lower() in bot_names)]

    merged_list = [pr for pr in prs if pr.get('mergedAt')]
    closed_only = [pr for pr in prs if not pr.get('mergedAt')]

    # Count merged RH PRs
    rh_merged = {}
    for pr in merged_list:
        login = pr.get('author',{}).get('login','').lower()
        if login in gh_users:
            emp = gh_users[login]
            rh_merged.setdefault(login, {'name': emp['name'],
                'tier': emp.get('github_resolution_tier',1), 'prs': []})
            rh_merged[login]['prs'].append(pr['number'])

    # Verify closed-only RH PRs (non-standard workflows)
    rh_landed = {}
    rh_dropped = {}
    if is_nonstandard and closed_only:
        candidates = {}
        for pr in closed_only:
            login = pr.get('author',{}).get('login','').lower()
            if login in gh_users:
                emp = gh_users[login]
                candidates.setdefault(login, {'name': emp['name'],
                    'tier': emp.get('github_resolution_tier',1), 'prs': []})
                candidates[login]['prs'].append(pr['number'])

        for login, info in candidates.items():
            rh_landed[login] = {'name': info['name'], 'tier': info['tier'], 'prs': []}
            rh_dropped[login] = []
            for num in info['prs']:
                try:
                    result = subprocess.run(
                        ['gh', 'api',
                         f'repos/{args.owner}/{args.repo}/issues/{num}/events',
                         '--jq', '[.[] | select(.event=="closed") | .commit_id // empty] | length'],
                        capture_output=True, text=True, timeout=30)
                    has_commit = (int(result.stdout.strip()) > 0
                                 if result.stdout.strip().isdigit() else False)
                except Exception:
                    has_commit = False
                if has_commit:
                    rh_landed[login]['prs'].append(num)
                else:
                    rh_dropped[login].append(num)

    total_merged = len(merged_list)
    total_closed = len(closed_only)
    total = total_merged + total_closed
    rh_merged_count = sum(len(v['prs']) for v in rh_merged.values())
    rh_landed_count = sum(len(v['prs']) for v in rh_landed.values())
    rh_total = rh_merged_count + rh_landed_count
    pct = round(rh_total / total * 100, 1) if total else 0

    print(f'Total PRs: {total} (merged: {total_merged}, closed-only: {total_closed})')
    print(f'RH total: {rh_total} ({pct}%) — merged: {rh_merged_count}, landed: {rh_landed_count}')

    all_rh = {}
    for login, info in rh_merged.items():
        all_rh.setdefault(login, {'name': info['name'], 'tier': info['tier'],
            'merged': 0, 'landed': 0, 'dropped': 0})
        all_rh[login]['merged'] = len(info['prs'])
    for login, info in rh_landed.items():
        all_rh.setdefault(login, {'name': info['name'], 'tier': info['tier'],
            'merged': 0, 'landed': 0, 'dropped': 0})
        all_rh[login]['landed'] = len(info['prs'])
        all_rh[login]['dropped'] = len(rh_dropped.get(login, []))

    for login, info in sorted(all_rh.items(), key=lambda x: -(x[1]['merged'] + x[1]['landed'])):
        t = info['merged'] + info['landed']
        parts = []
        if info['merged']: parts.append(f"{info['merged']} merged")
        if info['landed']: parts.append(f"{info['landed']} landed")
        if info['dropped']: parts.append(f"{info['dropped']} dropped")
        print(f"  {info['name']} (@{login}, T{info['tier']}): {t} PRs ({', '.join(parts)})")

    print(f'Coverage: {roster["resolution_coverage_pct"]}%')

    metadata = {
        'workflow_type': 'non-standard' if is_nonstandard else 'standard',
        'total_merged': total_merged, 'total_closed_only': total_closed,
        'total_prs': total, 'rh_merged_count': rh_merged_count,
        'rh_landed_count': rh_landed_count, 'rh_verified_total': rh_total,
        'rh_pct': pct,
        'per_employee': {l: {'merged': i['merged'], 'landed': i['landed'],
            'dropped': i['dropped']} for l, i in all_rh.items()}
    }
    json.dump(metadata, open(os.path.join(args.workdir, 'kpi1-metadata.json'), 'w'), indent=2)
    print(f'Metadata written to {args.workdir}/kpi1-metadata.json')

if __name__ == '__main__':
    main()
