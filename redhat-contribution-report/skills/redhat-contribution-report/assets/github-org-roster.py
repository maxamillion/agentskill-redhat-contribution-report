#!/usr/bin/env python3
"""Build an employee roster from a GitHub organization's member list.

Fetches org members via gh CLI, enriches with profile data, and writes
(or merges into) a roster JSON file compatible with the contribution
evaluation pipeline.
"""
import argparse, json, os, subprocess, sys


def gh_api(endpoint, jq=None, timeout=60):
    """Call gh api and return (stdout, returncode)."""
    cmd = ['gh', 'api', endpoint]
    if jq:
        cmd += ['--jq', jq]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode, r.stderr.strip()


def fetch_org_members(org):
    """Fetch all public members of a GitHub org. Returns list of logins."""
    stdout, rc, stderr = gh_api(
        f'orgs/{org}/members?per_page=100',
        jq='.[].login'
    )
    if rc != 0:
        if '404' in stderr or 'Not Found' in stderr:
            print(f'ERROR: Organization "{org}" not found or not accessible.', file=sys.stderr)
            print('Ensure the org exists and your gh token has access.', file=sys.stderr)
            sys.exit(1)
        if '403' in stderr or 'Forbidden' in stderr:
            print(f'ERROR: Access denied for organization "{org}".', file=sys.stderr)
            print('Private org membership requires org membership or admin token.', file=sys.stderr)
            sys.exit(1)
        print(f'ERROR: Failed to fetch org members: {stderr[:200]}', file=sys.stderr)
        sys.exit(1)

    # gh api with --jq on paginated endpoints may not paginate; use --paginate
    stdout, rc, stderr = gh_api(
        f'orgs/{org}/members?per_page=100',
        jq=None
    )
    if rc != 0:
        print(f'ERROR: Failed to fetch org members: {stderr[:200]}', file=sys.stderr)
        sys.exit(1)

    # Re-fetch with --paginate for full list
    cmd = ['gh', 'api', f'orgs/{org}/members', '--paginate',
           '--jq', '.[].login']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f'ERROR: Failed to paginate org members: {r.stderr[:200]}', file=sys.stderr)
        sys.exit(1)

    logins = [l.strip() for l in r.stdout.strip().split('\n') if l.strip()]
    return logins


def fetch_user_profile(login):
    """Fetch a user's profile. Returns dict or None on failure."""
    stdout, rc, stderr = gh_api(
        f'users/{login}',
        jq='{login: .login, name: .name, email: .email, company: .company, bio: .bio}'
    )
    if rc != 0:
        return None
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def build_employee_entry(profile, login):
    """Build a roster employee entry from a GitHub profile."""
    name = login
    email = None
    if profile:
        name = profile.get('name') or login
        email = profile.get('email')
    return {
        'name': name,
        'uid': None,
        'email': email,
        'title': None,
        'github_username': login,
        'github_resolution_method': 'github-org',
        'github_resolution_tier': 1,
        'depth': None,
        'source': 'github-org'
    }


def main():
    p = argparse.ArgumentParser(description='Build roster from GitHub org members')
    p.add_argument('--org', required=True, help='GitHub organization name')
    p.add_argument('--output', required=True, help='Output roster JSON path')
    p.add_argument('--merge', action='store_true',
                   help='Merge into existing roster file instead of overwriting')
    args = p.parse_args()

    # Fetch org members
    print(f'Fetching members of GitHub org: {args.org}')
    logins = fetch_org_members(args.org)
    print(f'Found {len(logins)} members')

    if len(logins) >= 500:
        print(f'WARNING: Large organization ({len(logins)} members). '
              f'Profile enrichment will make {len(logins)} API calls.')

    # Enrich with profile data
    employees = []
    for i, login in enumerate(logins):
        if (i + 1) % 50 == 0 or i == 0:
            print(f'  Enriching profiles: {i + 1}/{len(logins)}...')
        profile = fetch_user_profile(login)
        employees.append(build_employee_entry(profile, login))

    print(f'Built {len(employees)} employee entries from org members')

    # Merge or create roster
    if args.merge and os.path.exists(args.output):
        print(f'Merging with existing roster: {args.output}')
        roster = json.load(open(args.output))

        # Index existing employees by github_username (lowercase)
        existing_by_gh = {}
        for idx, e in enumerate(roster['employees']):
            gh = (e.get('github_username') or '').lower()
            if gh:
                existing_by_gh[gh] = idx

        # Index new org members by login (lowercase)
        org_logins = set()
        for emp in employees:
            login_lower = emp['github_username'].lower()
            org_logins.add(login_lower)

            if login_lower in existing_by_gh:
                # Update existing LDAP entry: mark as both sources
                idx = existing_by_gh[login_lower]
                roster['employees'][idx]['source'] = 'both'
                print(f'  Merged: {emp["github_username"]} (LDAP + org)')
            else:
                # New org member not in LDAP roster
                roster['employees'].append(emp)
                print(f'  Added: {emp["github_username"]} (org only)')

        # Mark LDAP-only employees (not in org)
        for e in roster['employees']:
            if e.get('source') not in ('both', 'github-org'):
                e.setdefault('source', 'ldap')

        roster['roster_source'] = 'both'
        roster['github_org'] = args.org
    else:
        # Create new roster from org members only
        from datetime import datetime, timezone
        roster = {
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'manager': None,
            'roster_source': 'github-org',
            'github_org': args.org,
            'total_employees': len(employees),
            'resolved_count': len(employees),
            'resolution_coverage_pct': 100.0,
            'employees': employees
        }

    # Update counts
    roster['total_employees'] = len(roster['employees'])
    roster['resolved_count'] = sum(
        1 for e in roster['employees'] if e.get('github_username'))
    roster['resolution_coverage_pct'] = round(
        roster['resolved_count'] / roster['total_employees'] * 100, 1
    ) if roster['total_employees'] > 0 else 0.0

    # Write output
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(roster, f, indent=2)
    print(f'\nRoster written to {args.output}')
    print(f'Total: {roster["total_employees"]} employees, '
          f'{roster["resolved_count"]} resolved '
          f'({roster["resolution_coverage_pct"]}%)')


if __name__ == '__main__':
    main()
