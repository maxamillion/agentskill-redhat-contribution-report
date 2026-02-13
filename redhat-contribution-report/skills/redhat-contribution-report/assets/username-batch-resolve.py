#!/usr/bin/env python3
"""Batch resolve GitHub usernames for unresolved Red Hat employees.

Searches git history across target projects for @redhat.com emails,
confirms matches via gh search commits, and optionally tries gh search users.
Updates the roster JSON file in place.
"""
import argparse, json, os, subprocess

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--roster', required=True)
    p.add_argument('--projects', required=True, help='Comma-separated owner/repo list')
    p.add_argument('--workdir', required=True)
    args = p.parse_args()

    roster = json.load(open(args.roster))
    unresolved = [e for e in roster['employees'] if not e.get('github_username')]
    projects = [x.strip() for x in args.projects.split(',') if x.strip()]

    print(f'Unresolved: {len(unresolved)}/{roster["total_employees"]}')
    if not unresolved:
        print('All employees already resolved.')
        return

    email_to_emp = {e['email'].lower(): e for e in unresolved if e.get('email')}
    resolved = {}  # email -> {uid, login, method, tier}

    # Step 1: Search git history across all projects for @redhat.com emails
    for proj in projects:
        owner, repo = proj.split('/')
        print(f'\nSearching {proj} git history...')
        r = subprocess.run(
            ['gh', 'api', f'repos/{owner}/{repo}/commits?per_page=100',
             '--paginate', '--jq',
             r'.[].commit | select(.author.email != null) | "\(.author.email)|\(.author.name)"'],
            capture_output=True, text=True, timeout=300)

        if r.returncode != 0:
            print(f'  WARNING: failed ({r.stderr.strip()[:100]})')
            continue

        rh_lines = set()
        for line in r.stdout.strip().split('\n'):
            if line and '@redhat.com' in line.lower():
                rh_lines.add(line)

        raw_path = os.path.join(args.workdir, f'raw-git-emails-{owner}-{repo}.txt')
        with open(raw_path, 'w') as f:
            f.write('\n'.join(sorted(rh_lines)))
        print(f'  Found {len(rh_lines)} unique @redhat.com entries')

        # Match emails and confirm GitHub usernames
        for line in rh_lines:
            parts = line.split('|', 1)
            if len(parts) != 2:
                continue
            email = parts[0].strip().lower()
            if email in email_to_emp and email not in resolved:
                emp = email_to_emp[email]
                cr = subprocess.run(
                    ['gh', 'search', 'commits', '--author-email', email,
                     '--repo', proj, '--limit', '1', '--json', 'author'],
                    capture_output=True, text=True, timeout=30)
                if cr.returncode == 0 and cr.stdout.strip():
                    try:
                        data = json.loads(cr.stdout)
                        if data and data[0].get('author', {}).get('login'):
                            login = data[0]['author']['login']
                            resolved[email] = {
                                'uid': emp['uid'], 'login': login,
                                'method': 'git-email', 'tier': 2
                            }
                            print(f'  CONFIRMED: {emp["name"]} -> @{login}')
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass

    # Step 2: gh search users for remaining (if <20)
    still_unresolved = [e for e in unresolved
                        if e.get('email', '').lower() not in resolved]

    if 0 < len(still_unresolved) <= 20:
        print(f'\nUser search for {len(still_unresolved)} remaining...')
        for emp in still_unresolved:
            r = subprocess.run(
                ['gh', 'search', 'users', emp['name'], '--limit', '5',
                 '--json', 'login,name,email,bio,company'],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                continue
            try:
                users = json.loads(r.stdout)
                for u in users:
                    name_match = u.get('name', '').lower() == emp['name'].lower()
                    rh_signal = any([
                        'red hat' in (u.get('company', '') or '').lower(),
                        'red hat' in (u.get('bio', '') or '').lower(),
                        '@redhat.com' in (u.get('email', '') or '').lower()
                    ])
                    if name_match and rh_signal:
                        resolved[emp['email'].lower()] = {
                            'uid': emp['uid'], 'login': u['login'],
                            'method': 'gh-search-users', 'tier': 3
                        }
                        print(f'  MATCHED: {emp["name"]} -> @{u["login"]}')
                        break
            except (json.JSONDecodeError, KeyError):
                pass
    elif len(still_unresolved) > 20:
        print(f'\nSkipping user search: {len(still_unresolved)} remaining (>20)')

    # Step 3: Update roster JSON
    for e in roster['employees']:
        for email, info in resolved.items():
            if e['uid'] == info['uid']:
                e['github_username'] = info['login']
                e['github_resolution_method'] = info['method']
                e['github_resolution_tier'] = info['tier']
                break

    total_resolved = sum(1 for e in roster['employees'] if e.get('github_username'))
    roster['resolved_count'] = total_resolved
    roster['resolution_coverage_pct'] = round(
        total_resolved / roster['total_employees'] * 100, 1)
    json.dump(roster, open(args.roster, 'w'), indent=2)

    print(f'\nNew resolutions: {len(resolved)}')
    print(f'Total: {total_resolved}/{roster["total_employees"]} '
          f'({roster["resolution_coverage_pct"]}%)')

if __name__ == '__main__':
    main()
