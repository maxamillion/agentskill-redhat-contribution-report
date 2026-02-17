#!/usr/bin/env python3
"""Final audit: validate report against checkpoints, roster, and scoring rubric.

Reads the generated report, checkpoint files, roster JSON, and scoring rubric
to cross-reference all claims. Outputs categorized findings (pass/warning/
discrepancy) and spot-check targets for the auditor agent to verify via gh CLI.
"""
import argparse, json, os, re, sys


def load_json(path):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'WARNING: Could not load {path}: {e}')
        return None


def extract_usernames_from_report(report_text):
    """Extract all @username references from the report."""
    return set(re.findall(r'@([\w-]+)', report_text))


def extract_scores_from_report(report_text):
    """Extract Score: N/5 entries with surrounding context for KPI identification."""
    scores = []
    for m in re.finditer(r'###\s+KPI\s+(\d).*?\n.*?Score:\s*(\d)/5', report_text, re.DOTALL):
        scores.append({'kpi': int(m.group(1)), 'score': int(m.group(2))})
    # Fallback: match any Score: N/5 patterns
    if not scores:
        for m in re.finditer(r'Score:\s*(\d)/5', report_text):
            scores.append({'kpi': None, 'score': int(m.group(1))})
    return scores


def extract_project_sections(report_text):
    """Split report into per-project sections."""
    sections = {}
    for m in re.finditer(r'^## ([\w-]+)/([\w-]+)\s*$', report_text, re.MULTILINE):
        owner, repo = m.group(1), m.group(2)
        start = m.start()
        # Find next ## section or end
        next_section = re.search(r'^## ', report_text[start + 1:], re.MULTILINE)
        end = start + 1 + next_section.start() if next_section else len(report_text)
        sections[f'{owner}/{repo}'] = report_text[start:end]
    return sections


def check_roster_attribution(report_text, roster):
    """Verify every @username in the report exists in the roster."""
    findings = []
    report_usernames = extract_usernames_from_report(report_text)
    roster_usernames = {e['github_username'].lower()
                        for e in roster['employees']
                        if e.get('github_username')}

    # Known bot/system accounts to ignore
    ignore = {'dependabot', 'renovate', 'mergify', 'github-actions',
              'pytorchmergebot', 'pytorchupdatebot', 'facebook-github-bot',
              'semantic-release-bot', 'release-please', 'goreleaser', 'pypi-bot'}

    for username in report_usernames:
        if username.lower() in ignore:
            continue
        if username.lower() in roster_usernames:
            findings.append({
                'check': 'roster_attribution',
                'status': 'pass',
                'detail': f'@{username} found in roster'
            })
        else:
            findings.append({
                'check': 'roster_attribution',
                'status': 'warning',
                'detail': f'@{username} in report but not in roster — '
                          'may be external contributor referenced for context'
            })
    return findings


def check_score_ranges(report_text):
    """Verify all scores are integers 1-5."""
    findings = []
    scores = extract_scores_from_report(report_text)
    for s in scores:
        kpi_label = f'KPI {s["kpi"]}' if s['kpi'] else 'unknown KPI'
        if 1 <= s['score'] <= 5:
            findings.append({
                'check': 'score_range',
                'status': 'pass',
                'detail': f'{kpi_label}: score {s["score"]}/5 is valid'
            })
        else:
            findings.append({
                'check': 'score_range',
                'status': 'discrepancy',
                'detail': f'{kpi_label}: score {s["score"]} is outside valid range 1-5'
            })
    return findings


def check_kpi1_rubric(project_section, metadata):
    """Cross-check KPI 1 score against rubric thresholds using metadata."""
    findings = []
    if not metadata:
        findings.append({
            'check': 'score_rubric_kpi1',
            'status': 'warning',
            'detail': 'No kpi1-metadata.json — cannot verify KPI 1 score'
        })
        return findings

    pct = metadata.get('rh_pct', 0)
    # Determine expected score from rubric thresholds
    if pct >= 30:
        expected = 5
    elif pct >= 20:
        expected = 4
    elif pct >= 10:
        expected = 3
    elif pct >= 1:
        expected = 2
    else:
        expected = 1

    # Find actual score in this section
    score_match = re.search(r'###\s+KPI\s+1.*?Score:\s*(\d)/5', project_section, re.DOTALL)
    if not score_match:
        findings.append({
            'check': 'score_rubric_kpi1',
            'status': 'warning',
            'detail': 'Could not extract KPI 1 score from report section'
        })
        return findings

    actual = int(score_match.group(1))
    if actual == expected:
        findings.append({
            'check': 'score_rubric_kpi1',
            'status': 'pass',
            'detail': f'KPI 1 score {actual}/5 matches rubric for {pct}% RH PRs'
        })
    else:
        findings.append({
            'check': 'score_rubric_kpi1',
            'status': 'discrepancy',
            'detail': f'KPI 1 score {actual}/5 but rubric expects {expected}/5 '
                      f'for {pct}% RH PRs'
        })

    return findings


def check_kpi3_rubric(project_section, governance_matches):
    """Cross-check KPI 3 score against governance match counts."""
    findings = []
    if not governance_matches:
        findings.append({
            'check': 'score_rubric_kpi3',
            'status': 'warning',
            'detail': 'No governance-matches.json — cannot verify KPI 3 score'
        })
        return findings

    # Count unique maintainer/approver matches (not reviewers)
    maintainer_roles = {'maintainer', 'approver', 'codeowner', 'committer', 'listed'}
    reviewer_only_roles = {'reviewer'}
    maintainer_count = len({m['login'].lower() for m in governance_matches
                           if m.get('role') in maintainer_roles})
    reviewer_count = len({m['login'].lower() for m in governance_matches
                         if m.get('role') in reviewer_only_roles})

    if maintainer_count >= 3:
        expected = 5
    elif maintainer_count >= 2:
        expected = 4
    elif maintainer_count >= 1:
        expected = 3
    elif reviewer_count >= 1:
        expected = 2
    else:
        expected = 1

    score_match = re.search(r'###\s+KPI\s+3.*?Score:\s*(\d)/5', project_section, re.DOTALL)
    if not score_match:
        findings.append({
            'check': 'score_rubric_kpi3',
            'status': 'warning',
            'detail': 'Could not extract KPI 3 score from report section'
        })
        return findings

    actual = int(score_match.group(1))
    if actual == expected:
        findings.append({
            'check': 'score_rubric_kpi3',
            'status': 'pass',
            'detail': f'KPI 3 score {actual}/5 matches rubric for '
                      f'{maintainer_count} maintainers/{reviewer_count} reviewers'
        })
    else:
        findings.append({
            'check': 'score_rubric_kpi3',
            'status': 'discrepancy',
            'detail': f'KPI 3 score {actual}/5 but rubric expects {expected}/5 '
                      f'for {maintainer_count} maintainers/{reviewer_count} reviewers'
        })

    return findings


def check_checkpoint_existence(workdir, project):
    """Verify all 5 KPI checkpoint files exist for a project."""
    findings = []
    owner, repo = project.split('/')
    project_dir = os.path.join(workdir, f'{owner}-{repo}')

    expected_files = [
        'kpi1-pr-contributions.md',
        'kpi2-release-management.md',
        'kpi3-maintainership.md',
        'kpi4-roadmap-influence.md',
        'kpi5-leadership.md',
    ]

    for f in expected_files:
        path = os.path.join(project_dir, f)
        if os.path.exists(path):
            findings.append({
                'check': 'checkpoint_existence',
                'status': 'pass',
                'detail': f'{project}: {f} exists'
            })
        else:
            findings.append({
                'check': 'checkpoint_existence',
                'status': 'discrepancy',
                'detail': f'{project}: {f} MISSING'
            })

    return findings


def check_kpi1_metadata_crossref(project_section, metadata):
    """Compare reported PR counts against kpi1-metadata.json values."""
    findings = []
    if not metadata:
        return findings

    rh_total = metadata.get('rh_verified_total', 0)
    rh_pct = metadata.get('rh_pct', 0)
    total_prs = metadata.get('total_prs', 0)

    # Look for reported PR counts in the section
    pr_count_match = re.search(r'Red Hat Authored PRs:\*\*\s*(\d+)', project_section)
    if pr_count_match:
        reported = int(pr_count_match.group(1))
        if reported == rh_total:
            findings.append({
                'check': 'kpi1_crossref',
                'status': 'pass',
                'detail': f'Reported RH PR count ({reported}) matches metadata ({rh_total})'
            })
        else:
            findings.append({
                'check': 'kpi1_crossref',
                'status': 'discrepancy',
                'detail': f'Reported RH PR count ({reported}) != metadata ({rh_total})'
            })
    else:
        findings.append({
            'check': 'kpi1_crossref',
            'status': 'warning',
            'detail': 'Could not extract RH PR count from report for cross-reference'
        })

    # Check percentage if total is available for context
    if total_prs > 0:
        pct_match = re.search(r'Red Hat Authored PRs:\*\*\s*\d+\s*\((\d+(?:\.\d+)?)%\)', project_section)
        if pct_match:
            reported_pct = float(pct_match.group(1))
            if abs(reported_pct - rh_pct) <= 1.0:
                findings.append({
                    'check': 'kpi1_crossref',
                    'status': 'pass',
                    'detail': f'Reported RH PR pct ({reported_pct}%) matches metadata ({rh_pct}%)'
                })
            else:
                findings.append({
                    'check': 'kpi1_crossref',
                    'status': 'discrepancy',
                    'detail': f'Reported RH PR pct ({reported_pct}%) != metadata ({rh_pct}%)'
                })

    return findings


def check_confidence_levels(report_text, roster):
    """Verify stated confidence levels respect min(resolution_tier, data_confidence)."""
    findings = []
    # Build tier map from roster
    tier_map = {}
    for e in roster['employees']:
        if e.get('github_username'):
            tier_map[e['github_username'].lower()] = e.get('github_resolution_tier')

    # Find confidence statements with associated usernames
    # Pattern: @username ... Confidence: High/Medium/Low
    for m in re.finditer(
        r'@([\w-]+).*?(?:Confidence|Tier).*?(High|Medium|Low)',
        report_text, re.DOTALL
    ):
        username = m.group(1).lower()
        stated = m.group(2)
        tier = tier_map.get(username)

        if tier is None:
            continue

        # Tier 3 can only be Low
        if tier == 3 and stated != 'Low':
            findings.append({
                'check': 'confidence_level',
                'status': 'discrepancy',
                'detail': f'@{username} is Tier 3 but confidence stated as {stated} '
                          '(should be capped at Low)'
            })
        elif tier == 2 and stated == 'High':
            findings.append({
                'check': 'confidence_level',
                'status': 'discrepancy',
                'detail': f'@{username} is Tier 2 but confidence stated as High '
                          '(should be capped at Medium)'
            })
        else:
            findings.append({
                'check': 'confidence_level',
                'status': 'pass',
                'detail': f'@{username} Tier {tier} with {stated} confidence — valid'
            })

    if not findings:
        findings.append({
            'check': 'confidence_level',
            'status': 'warning',
            'detail': 'Could not extract per-employee confidence levels for verification'
        })

    return findings


def select_spot_checks(report_text, projects):
    """Pick up to 4 specific gh CLI commands for the agent to verify."""
    targets = []

    # 1. Find PR numbers mentioned in report
    pr_refs = re.findall(r'#(\d{2,6})', report_text)
    if pr_refs:
        # Pick first PR from first project context
        for project in projects:
            # Find PR numbers near this project name
            pattern = re.escape(project) + r'.*?#(\d{2,6})'
            m = re.search(pattern, report_text, re.DOTALL)
            if m:
                targets.append({
                    'type': 'pr_verify',
                    'command': f'gh pr view {m.group(1)} --repo {project} '
                               '--json state,mergedAt,author',
                    'description': f'Verify PR #{m.group(1)} in {project}'
                })
                break
        if not targets and pr_refs:
            targets.append({
                'type': 'pr_verify',
                'command': f'gh pr view {pr_refs[0]} --repo {projects[0]} '
                           '--json state,mergedAt,author',
                'description': f'Verify PR #{pr_refs[0]} in {projects[0]}'
            })

    # 2. Find release tags mentioned
    tag_refs = re.findall(r'v\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?', report_text)
    if tag_refs:
        targets.append({
            'type': 'release_verify',
            'command': f'gh api repos/{projects[0]}/releases/tags/{tag_refs[0]}',
            'description': f'Verify release tag {tag_refs[0]} in {projects[0]}'
        })

    # 3. Verify a contributor username exists on GitHub
    report_usernames = extract_usernames_from_report(report_text)
    contributor_usernames = [u for u in report_usernames
                            if u.lower() not in {'dependabot', 'renovate', 'mergify',
                                                  'github-actions'}]
    if contributor_usernames:
        u = list(contributor_usernames)[0]
        targets.append({
            'type': 'user_verify',
            'command': f'gh api users/{u} --jq ".login"',
            'description': f'Verify GitHub user @{u} exists'
        })

    # 4. Check a governance file if referenced
    gov_refs = re.findall(r'(OWNERS|CODEOWNERS|MAINTAINERS)(?:\s+(?:file|path))?',
                          report_text)
    if gov_refs and projects:
        targets.append({
            'type': 'governance_verify',
            'command': f'gh api repos/{projects[0]}/contents/{gov_refs[0]} '
                       '--jq ".name"',
            'description': f'Verify {gov_refs[0]} exists in {projects[0]}'
        })

    return targets[:4]


def main():
    p = argparse.ArgumentParser(description='Validate report against checkpoints and rubric')
    p.add_argument('--report', required=True, help='Path to the generated report')
    p.add_argument('--roster', required=True, help='Path to employee-roster.json')
    p.add_argument('--workdir', required=True, help='Working directory with checkpoint files')
    p.add_argument('--rubric', required=True, help='Path to scoring-rubric.json')
    p.add_argument('--projects', required=True,
                   help='Comma-separated list of owner/repo projects')
    args = p.parse_args()

    projects = [p.strip() for p in args.projects.split(',') if p.strip()]

    # Load inputs
    try:
        report_text = open(args.report).read()
    except FileNotFoundError:
        print(f'ERROR: Report file not found: {args.report}')
        sys.exit(1)

    roster = load_json(args.roster)
    if not roster:
        print('ERROR: Could not load roster')
        sys.exit(1)

    rubric = load_json(args.rubric)
    if not rubric:
        print('WARNING: Could not load rubric — using built-in thresholds')

    all_findings = []

    # 1. Roster attribution
    print('=== Roster Attribution Check ===')
    attr_findings = check_roster_attribution(report_text, roster)
    all_findings.extend(attr_findings)
    for f in attr_findings:
        if f['status'] != 'pass':
            print(f'  [{f["status"].upper()}] {f["detail"]}')
    attr_pass = sum(1 for f in attr_findings if f['status'] == 'pass')
    print(f'  {attr_pass}/{len(attr_findings)} usernames verified in roster')

    # 2. Score range validation
    print('\n=== Score Range Validation ===')
    range_findings = check_score_ranges(report_text)
    all_findings.extend(range_findings)
    for f in range_findings:
        print(f'  [{f["status"].upper()}] {f["detail"]}')

    # 3. Confidence level verification
    print('\n=== Confidence Level Verification ===')
    conf_findings = check_confidence_levels(report_text, roster)
    all_findings.extend(conf_findings)
    for f in conf_findings:
        if f['status'] != 'pass':
            print(f'  [{f["status"].upper()}] {f["detail"]}')
    conf_pass = sum(1 for f in conf_findings if f['status'] == 'pass')
    print(f'  {conf_pass}/{len(conf_findings)} confidence levels valid')

    # Per-project checks
    project_sections = extract_project_sections(report_text)
    for project in projects:
        owner, repo = project.split('/')
        project_dir = os.path.join(args.workdir, f'{owner}-{repo}')
        section = project_sections.get(project, '')

        print(f'\n=== {project} ===')

        # 4. Checkpoint existence
        cp_findings = check_checkpoint_existence(args.workdir, project)
        all_findings.extend(cp_findings)
        cp_missing = [f for f in cp_findings if f['status'] != 'pass']
        if cp_missing:
            for f in cp_missing:
                print(f'  [{f["status"].upper()}] {f["detail"]}')
        else:
            print(f'  All 5 checkpoint files present')

        if not section:
            all_findings.append({
                'check': 'project_section',
                'status': 'warning',
                'detail': f'{project}: no matching section found in report'
            })
            print(f'  [WARNING] No matching section found in report')
            continue

        # 5. KPI 1 rubric consistency
        kpi1_meta = load_json(os.path.join(project_dir, 'kpi1-metadata.json'))
        rubric_findings = check_kpi1_rubric(section, kpi1_meta)
        all_findings.extend(rubric_findings)
        for f in rubric_findings:
            print(f'  [{f["status"].upper()}] {f["detail"]}')

        # 6. KPI 3 rubric consistency
        gov_matches = load_json(os.path.join(project_dir, 'governance-matches.json'))
        gov_findings = check_kpi3_rubric(section, gov_matches)
        all_findings.extend(gov_findings)
        for f in gov_findings:
            print(f'  [{f["status"].upper()}] {f["detail"]}')

        # 7. KPI 1 metadata cross-reference
        xref_findings = check_kpi1_metadata_crossref(section, kpi1_meta)
        all_findings.extend(xref_findings)
        for f in xref_findings:
            if f['status'] != 'pass':
                print(f'  [{f["status"].upper()}] {f["detail"]}')

    # 8. Spot-check target selection
    spot_checks = select_spot_checks(report_text, projects)

    # Summary
    total = len(all_findings)
    passes = sum(1 for f in all_findings if f['status'] == 'pass')
    warnings = sum(1 for f in all_findings if f['status'] == 'warning')
    discrepancies = sum(1 for f in all_findings if f['status'] == 'discrepancy')

    print(f'\n=== AUDIT SUMMARY ===')
    print(f'Total checks: {total}')
    print(f'  Pass: {passes}')
    print(f'  Warnings: {warnings}')
    print(f'  Discrepancies: {discrepancies}')

    if discrepancies > 0:
        print(f'\nDISCREPANCIES FOUND:')
        for f in all_findings:
            if f['status'] == 'discrepancy':
                print(f'  - [{f["check"]}] {f["detail"]}')

    if spot_checks:
        print(f'\nSpot-check targets ({len(spot_checks)}):')
        for sc in spot_checks:
            print(f'  - {sc["description"]}')
            print(f'    {sc["command"]}')

    # Write results JSON
    results = {
        'total_checks': total,
        'passes': passes,
        'warnings': warnings,
        'discrepancies': discrepancies,
        'findings': all_findings,
        'spot_check_targets': spot_checks,
        'summary': {
            'roster_attribution': {
                'pass': sum(1 for f in all_findings if f['check'] == 'roster_attribution' and f['status'] == 'pass'),
                'warning': sum(1 for f in all_findings if f['check'] == 'roster_attribution' and f['status'] == 'warning'),
                'discrepancy': sum(1 for f in all_findings if f['check'] == 'roster_attribution' and f['status'] == 'discrepancy'),
            },
            'score_consistency': {
                'pass': sum(1 for f in all_findings if f['check'] in ('score_range', 'score_rubric_kpi1', 'score_rubric_kpi3') and f['status'] == 'pass'),
                'warning': sum(1 for f in all_findings if f['check'] in ('score_range', 'score_rubric_kpi1', 'score_rubric_kpi3') and f['status'] == 'warning'),
                'discrepancy': sum(1 for f in all_findings if f['check'] in ('score_range', 'score_rubric_kpi1', 'score_rubric_kpi3') and f['status'] == 'discrepancy'),
            },
            'confidence_levels': {
                'pass': sum(1 for f in all_findings if f['check'] == 'confidence_level' and f['status'] == 'pass'),
                'warning': sum(1 for f in all_findings if f['check'] == 'confidence_level' and f['status'] == 'warning'),
                'discrepancy': sum(1 for f in all_findings if f['check'] == 'confidence_level' and f['status'] == 'discrepancy'),
            },
            'data_crossref': {
                'pass': sum(1 for f in all_findings if f['check'] in ('checkpoint_existence', 'kpi1_crossref') and f['status'] == 'pass'),
                'warning': sum(1 for f in all_findings if f['check'] in ('checkpoint_existence', 'kpi1_crossref') and f['status'] == 'warning'),
                'discrepancy': sum(1 for f in all_findings if f['check'] in ('checkpoint_existence', 'kpi1_crossref') and f['status'] == 'discrepancy'),
            },
        }
    }

    output_path = os.path.join(args.workdir, 'audit-results.json')
    json.dump(results, open(output_path, 'w'), indent=2)
    print(f'\nResults written to {output_path}')


if __name__ == '__main__':
    main()
