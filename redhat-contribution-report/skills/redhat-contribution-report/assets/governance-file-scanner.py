#!/usr/bin/env python3
"""Scan governance files in a GitHub repo and match against employee roster.

Fetches all matching files from the repo tree in a single process,
extracts usernames, and matches against the roster. Used by KPI 3 and KPI 5.
"""
import argparse, base64, json, os, re, subprocess, sys

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--owner', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--workdir', required=True)
    p.add_argument('--roster', required=True)
    p.add_argument('--pattern',
                   default='OWNERS|CODEOWNERS|MAINTAINERS|COMMITTER',
                   help='Regex pattern for file name matching')
    args = p.parse_args()

    roster = json.load(open(args.roster))
    gh_users = {e['github_username'].lower(): e
                for e in roster['employees'] if e.get('github_username')}

    # Find governance files via repo tree
    r = subprocess.run(
        ['gh', 'api',
         f'repos/{args.owner}/{args.repo}/git/trees/HEAD?recursive=1',
         '--jq',
         f'.tree[] | select(.path | test("{args.pattern}"; "i")) | .path'],
        capture_output=True, text=True, timeout=60)

    if r.returncode != 0:
        print(f'ERROR: tree listing failed: {r.stderr.strip()[:100]}')
        sys.exit(1)

    paths = [line.strip() for line in r.stdout.strip().split('\n') if line.strip()]
    print(f'Found {len(paths)} governance files')

    all_matches = []
    for filepath in paths:
        fr = subprocess.run(
            ['gh', 'api',
             f'repos/{args.owner}/{args.repo}/contents/{filepath}',
             '--jq', '.content'],
            capture_output=True, text=True, timeout=30)

        if fr.returncode != 0:
            print(f'  SKIP {filepath}: fetch failed')
            continue

        try:
            content = base64.b64decode(fr.stdout.strip()).decode('utf-8', errors='replace')
        except Exception:
            print(f'  SKIP {filepath}: decode failed')
            continue

        safe_name = filepath.replace('/', '-').replace(' ', '_')
        raw_path = os.path.join(args.workdir, f'raw-governance-{safe_name}.txt')
        with open(raw_path, 'w') as f:
            f.write(content)

        # Extract usernames
        usernames = set(re.findall(r'@?([\w-]+)', content))
        lower_path = filepath.lower()
        for u in usernames:
            if u.lower() in gh_users:
                emp = gh_users[u.lower()]
                # Classify role from file type
                if 'codeowners' in lower_path:
                    role = 'codeowner'
                elif 'maintainer' in lower_path:
                    role = 'maintainer'
                elif 'committer' in lower_path:
                    role = 'committer'
                else:
                    # OWNERS file — check section context
                    idx = content.lower().find(u.lower())
                    before = content[:idx].lower() if idx > 0 else ''
                    if 'approver' in before.split('\n')[-5:] or 'approvers' in before:
                        role = 'approver'
                    elif 'reviewer' in before:
                        role = 'reviewer'
                    else:
                        role = 'listed'

                match = {
                    'name': emp['name'], 'login': u,
                    'tier': emp.get('github_resolution_tier', 1),
                    'file': filepath, 'role': role
                }
                all_matches.append(match)
                print(f'  MATCH: {emp["name"]} (@{u}) T{match["tier"]} '
                      f'in {filepath} ({role})')

    summary_path = os.path.join(args.workdir, 'governance-matches.json')
    json.dump(all_matches, open(summary_path, 'w'), indent=2)
    print(f'\nTotal: {len(all_matches)} roster matches in governance files')
    print(f'Summary: {summary_path}')

if __name__ == '__main__':
    main()
