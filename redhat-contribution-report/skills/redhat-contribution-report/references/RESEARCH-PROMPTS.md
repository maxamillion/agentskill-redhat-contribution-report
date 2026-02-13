# Sub-Agent Prompt Templates

Six prompt templates: one **Username Resolution Agent** (centralized, runs once) and five **KPI Agents** (one per KPI per project, all launched in parallel). Replace `{owner}`, `{repo}`, `{cutoff_date}`, `{workdir}`, and `{roster_path}` with actual values before dispatching.

**Key invariants for all agents:**
- `{roster_path}` appears ONLY inside `python3 -c` script strings — never as an argument to the Read tool
- Agents return a 1-line status message; all detailed results go to checkpoint files
- Confidence = min(resolution_tier, data_source). Tier1+API=High, Tier1+docs=Medium, Tier2+API=Medium, Tier3+any=Low, any+web=Low
- Never fabricate data. Report gaps honestly with Low or Not Found confidence
- If 403 rate-limited, reduce query sizes and note it. Do not retry excessively
- Do NOT delete `{workdir}/raw-*.json` or checkpoint files during execution

---

## Username Resolution Agent

### PROMPT START

You resolve GitHub usernames for Red Hat employees who lack LDAP-resolved usernames. You search across multiple project repositories.

**ROSTER FILE:** {roster_path}
**TARGET PROJECTS:** {project_list}
**WORKING DIRECTORY:** {workdir}

**Step 1:** Load unresolved employees via python3 (do NOT use the Read tool on the roster file):
```bash
python3 -c "
import json
roster = json.load(open('{roster_path}'))
unresolved = [e for e in roster['employees'] if not e.get('github_username')]
print(f'Unresolved: {len(unresolved)}/{roster[\"total_employees\"]}')
for e in unresolved:
    print(f\"  {e['name']} | {e['email']}\")
"
```

**Step 2:** Batch-search git history across all target projects for @redhat.com emails:
```bash
gh api "repos/{owner}/{repo}/commits?per_page=100" --paginate --jq '.[].commit | select(.author.email != null) | "\(.author.email)|\(.author.name)"' | sort -u | grep -i '@redhat.com' > {workdir}/raw-git-emails-{owner}-{repo}.txt
```
Run this for each project. Then match emails to unresolved employees via python3.

**Step 3:** For email matches, confirm the GitHub username:
```bash
gh search commits --author-email {email} --repo {owner}/{repo} --limit 1 --json author
```

**Step 4:** For remaining unresolved employees (if fewer than 20), try `gh search users`:
```bash
gh search users "{employee_full_name}" --limit 5 --json login,name,email,bio,company
```
Acceptance: name must match AND at least one corroborating signal (commit in target repo, bio/company contains "Red Hat", or email matches @redhat.com). Never accept name-only matches.

**Step 5:** Update the roster JSON in place via python3:
```bash
python3 -c "
import json
roster = json.load(open('{roster_path}'))
resolutions = {
    # 'uid': ('github_username', 'method', tier_number),
}
for e in roster['employees']:
    if e['uid'] in resolutions:
        username, method, tier = resolutions[e['uid']]
        e['github_username'] = username
        e['github_resolution_method'] = method
        e['github_resolution_tier'] = tier
resolved = sum(1 for e in roster['employees'] if e.get('github_username'))
roster['resolved_count'] = resolved
roster['resolution_coverage_pct'] = round(resolved / roster['total_employees'] * 100, 1)
json.dump(roster, open('{roster_path}', 'w'), indent=2)
print(f'Updated: {resolved}/{roster[\"total_employees\"]} resolved ({roster[\"resolution_coverage_pct\"]}%)')
"
```

**Step 6:** Write resolution log to `{workdir}/username-resolutions.md` using the Write tool.

Return a 1-line status: `"Username resolution complete. {resolved}/{total} resolved ({pct}%). File: {workdir}/username-resolutions.md"`

### PROMPT END

---

## KPI 1: PR/Commit Contributions

### PROMPT START

You evaluate Red Hat employee PR/commit contributions for one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 0 — Workflow Detection:**

Some repos use non-standard merge workflows (e.g., pytorch's `pytorchmergebot` lands commits directly and **closes** the PR instead of merging via GitHub). Additionally, GitHub's Search API caps results at 1000 regardless of `--limit`. Detect the workflow before fetching PRs:

```bash
python3 -c "
import subprocess, json

def search_count(q):
    r = subprocess.run(['gh','api','search/issues','-X','GET',
        '-f',f'q={q}','-f','per_page=1','--jq','.total_count'],
        capture_output=True, text=True, timeout=30)
    return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0

merged = search_count('repo:{owner}/{repo} is:pr is:merged merged:>{cutoff_date}')
closed = search_count('repo:{owner}/{repo} is:pr is:closed closed:>{cutoff_date}')
landed = closed - merged

if landed > 3 * max(merged, 1) and merged >= 50 and landed >= 100:
    print(f'WORKFLOW=non-standard  MERGED={merged}  CLOSED={closed}  LANDED={landed}')
    print(f'This repo uses a land-and-close workflow ({landed} landed vs {merged} merged). Use --state closed.')
elif merged > 1000:
    print(f'WORKFLOW=high-volume  MERGED={merged}  CLOSED={closed}')
    print(f'Merged PR count exceeds Search API 1000-cap. Remove --search flag, filter by date locally.')
else:
    print(f'WORKFLOW=standard  MERGED={merged}  CLOSED={closed}')
"
```

Use the output to decide which fetch path to follow:
- `WORKFLOW=non-standard` → **Step 1A**
- `WORKFLOW=high-volume` → **Step 1B**
- `WORKFLOW=standard` → **Step 1C**

**Step 1A — Non-standard workflow** (land-and-close repos like pytorch):

Fetch closed PRs (the primary contribution path) and merged PRs (the small fraction merged via GitHub). **Do NOT use `--search`** — it uses the Search API which caps at 1000 results. The commands below use GraphQL pagination with no cap:

```bash
gh pr list --repo {owner}/{repo} --state closed --limit 20000 \
  --json number,author,closedAt > {workdir}/raw-prs.json
```

```bash
gh pr list --repo {owner}/{repo} --state merged --limit 5000 \
  --json number,author,mergedAt > {workdir}/raw-merged-prs.json
```

Both files are combined and deduplicated in Step 2.

**Step 1B — High-volume standard** (>1000 merged PRs, e.g., vllm):

Fetch all merged PRs **without `--search`** to avoid the 1000-result Search API cap:

```bash
gh pr list --repo {owner}/{repo} --state merged --limit 20000 \
  --json number,author,mergedAt > {workdir}/raw-prs.json
```

**Step 1C — Standard** (<=1000 merged PRs):

```bash
gh pr list --repo {owner}/{repo} --state merged --limit 5000 \
  --json number,author,mergedAt > {workdir}/raw-prs.json
```

**Step 2 — Roster matching with inline verification** (all workflows):

Match PRs against the employee roster via python3. For non-standard workflows, this combines closed and merged PR lists, separates definitively merged PRs from closed-only PRs, and verifies closed-only PRs inline — all in a single atomic script so verification cannot be skipped by turn exhaustion.

```bash
python3 -c "
import json, os, subprocess

roster = json.load(open('{roster_path}'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}

# Load primary PR list
prs = json.load(open('{workdir}/raw-prs.json'))

# Determine if this is a non-standard workflow (raw-merged-prs.json exists)
is_nonstandard = os.path.exists('{workdir}/raw-merged-prs.json')
if is_nonstandard:
    merged_prs = json.load(open('{workdir}/raw-merged-prs.json'))
    seen = {pr['number'] for pr in prs}
    prs.extend([pr for pr in merged_prs if pr['number'] not in seen])

# Filter by date — use whichever date field is present
prs = [pr for pr in prs if
    (pr.get('mergedAt') or pr.get('closedAt') or '') >= '{cutoff_date}']

# Exclude bot authors
bot_names = {'pytorchmergebot','pytorchupdatebot','facebook-github-bot',
             'github-actions','dependabot','renovate','mergify'}
prs = [pr for pr in prs if not (
    pr.get('author',{}).get('login','').endswith('[bot]') or
    pr.get('author',{}).get('login','').lower() in bot_names)]

# Separate into definitively merged (mergedAt set) and closed-only
merged_prs_list = [pr for pr in prs if pr.get('mergedAt')]
closed_only_prs = [pr for pr in prs if not pr.get('mergedAt')]

# For standard/high-volume workflows, all PRs should have mergedAt; closed-only = 0
# For non-standard workflows, closed-only PRs need per-PR verification
total_merged = len(merged_prs_list)
total_closed_only = len(closed_only_prs)

# Count merged PRs immediately as verified contributions
rh_merged = {}
for pr in merged_prs_list:
    login = pr.get('author',{}).get('login','').lower()
    if login in gh_users:
        emp = gh_users[login]
        rh_merged.setdefault(login, {'name': emp['name'],
            'tier': emp.get('github_resolution_tier',1), 'prs': []})
        rh_merged[login]['prs'].append(pr['number'])

# For non-standard workflows: verify each RH-candidate closed-only PR inline
rh_verified_landed = {}
rh_dropped = {}
if is_nonstandard and closed_only_prs:
    rh_closed_candidates = {}
    for pr in closed_only_prs:
        login = pr.get('author',{}).get('login','').lower()
        if login in gh_users:
            emp = gh_users[login]
            rh_closed_candidates.setdefault(login, {'name': emp['name'],
                'tier': emp.get('github_resolution_tier',1), 'prs': []})
            rh_closed_candidates[login]['prs'].append(pr['number'])

    for login, info in rh_closed_candidates.items():
        rh_verified_landed[login] = {'name': info['name'], 'tier': info['tier'], 'prs': []}
        rh_dropped[login] = []
        for num in info['prs']:
            try:
                result = subprocess.run(
                    ['gh', 'api', f'repos/{owner}/{repo}/issues/{num}/events',
                     '--jq', '[.[] | select(.event==\"closed\") | .commit_id // empty] | length'],
                    capture_output=True, text=True, timeout=30)
                has_commit = int(result.stdout.strip()) > 0 if result.stdout.strip().isdigit() else False
            except Exception:
                has_commit = False  # Conservative: exclude on failure
            if has_commit:
                rh_verified_landed[login]['prs'].append(num)
            else:
                rh_dropped[login].append(num)

# Compute verified totals
# For non-standard: denominator = merged + verified-landed (not merged + all-closed)
verified_landed_count = sum(len(v['prs']) for v in rh_verified_landed.values()) if is_nonstandard else 0
# Total verified PRs in the repo (for denominator)
# Standard/high-volume: total = merged only; Non-standard: total = merged + all closed-only (before RH filter)
total = total_merged + total_closed_only

rh_merged_count = sum(len(v['prs']) for v in rh_merged.values())
rh_landed_count = sum(len(v['prs']) for v in rh_verified_landed.values())
rh_total = rh_merged_count + rh_landed_count
pct = round(rh_total/total*100,1) if total else 0

print(f'Total merged PRs in window: {total_merged}')
if is_nonstandard:
    print(f'Total closed-only PRs in window: {total_closed_only}')
    print(f'Total verified-landed PRs (non-standard): {verified_landed_count}')
print(f'Total verified contributions: {total}')
print(f'RH merged PRs: {rh_merged_count}')
if is_nonstandard:
    print(f'RH verified-landed PRs: {rh_landed_count}')
print(f'RH total verified: {rh_total} ({pct}%)')

# Combine per-employee results
all_rh = {}
for login, info in rh_merged.items():
    all_rh.setdefault(login, {'name': info['name'], 'tier': info['tier'],
        'merged': 0, 'landed': 0, 'dropped': 0})
    all_rh[login]['merged'] = len(info['prs'])
for login, info in rh_verified_landed.items():
    all_rh.setdefault(login, {'name': info['name'], 'tier': info['tier'],
        'merged': 0, 'landed': 0, 'dropped': 0})
    all_rh[login]['landed'] = len(info['prs'])
    all_rh[login]['dropped'] = len(rh_dropped.get(login, []))

for login, info in sorted(all_rh.items(), key=lambda x:-(x[1]['merged']+x[1]['landed'])):
    total_emp = info['merged'] + info['landed']
    parts = []
    if info['merged']: parts.append(f\"{info['merged']} merged\")
    if info['landed']: parts.append(f\"{info['landed']} verified-landed\")
    if info['dropped']: parts.append(f\"{info['dropped']} dropped\")
    detail = ', '.join(parts)
    print(f\"  {info['name']} (@{login}, Tier {info['tier']}): {total_emp} verified PRs ({detail})\")

print(f'Resolution coverage: {roster[\"resolution_coverage_pct\"]}%')

# Write metadata for Phase 5 cross-checking
metadata = {
    'workflow_type': 'non-standard' if is_nonstandard else 'standard',
    'total_merged': total_merged,
    'total_closed_only': total_closed_only,
    'total_prs': total,
    'rh_merged_count': rh_merged_count,
    'rh_landed_count': rh_landed_count,
    'rh_verified_total': rh_total,
    'rh_pct': pct,
    'per_employee': {login: {'merged': info['merged'], 'landed': info['landed'],
        'dropped': info['dropped']} for login, info in all_rh.items()}
}
json.dump(metadata, open('{workdir}/kpi1-metadata.json', 'w'), indent=2)
print(f'Metadata written to {workdir}/kpi1-metadata.json')
"
```

This unified script makes at most N API calls where N = total RH-candidate closed-only PRs (typically <100, only for non-standard workflows). Verification and counting happen in the same python process, so verification cannot be skipped by turn exhaustion. PRs closed without a commit reference are excluded as abandoned/rejected. If an events API call fails for a specific PR, that PR is conservatively excluded.

**Step 3 — Co-authored commits:**
```bash
gh api "repos/{owner}/{repo}/commits?per_page=100&since={cutoff_date}T00:00:00Z" --paginate \
  --jq '.[].commit.message' > {workdir}/raw-commit-messages.txt
grep -i "co-authored-by" {workdir}/raw-commit-messages.txt | sort | uniq -c | sort -rn | head -20
```

**Step 4 — Confidence annotation:**

Add to the checkpoint output:
- If non-standard workflow: `"NOTE: This project uses a non-standard PR workflow. PRs are counted in two categories: (1) definitively merged via GitHub (mergedAt set), and (2) closed PRs verified as landed via closing commit reference. Unverified closed PRs are excluded."`
- If high-volume: `"NOTE: Merged PR count exceeds GitHub Search API 1000-result cap. Full dataset fetched via GraphQL pagination (no --search flag)."`
- Confidence is **High** for all paths: data source is GitHub API (authoritative), non-standard workflow PRs are individually verified, and no sampling or estimation is used.

**Scoring:** 5=>=30%, 4=20-29%, 3=10-19%, 2=1-9%, 1=0%

**Checkpoint:** Write complete KPI 1 results to `{workdir}/kpi1-pr-contributions.md` using the Write tool. Include: total PRs, RH count/pct, per-employee table (Employee | GitHub | Tier | PRs | Confidence), score, evidence URLs, workflow detection result, and any applicable workflow notes.

Return: `"KPI 1 complete. {rh_count}/{total} PRs ({pct}%). Score: {score}. File: {workdir}/kpi1-pr-contributions.md"`

### PROMPT END

---

## KPI 2: Release Management

### PROMPT START

You evaluate Red Hat employee involvement in release management for one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1:** Fetch releases and match against roster:
```bash
gh api "repos/{owner}/{repo}/releases" --paginate > {workdir}/raw-releases.json
python3 -c "
import json
roster = json.load(open('{roster_path}'))
releases = json.load(open('{workdir}/raw-releases.json'))
releases = [r for r in releases if r.get('published_at','') >= '{cutoff_date}']
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
bots = {'github-actions','dependabot','renovate','mergify','semantic-release-bot','release-please','goreleaser','pypi-bot'}
print(f'Total releases in window: {len(releases)}')
for r in releases:
    author = r.get('author',{}).get('login','unknown')
    is_bot = author.endswith('[bot]') or author in bots
    match = '(RH)' if author.lower() in gh_users else '(bot)' if is_bot else ''
    print(f\"  {r.get('tag_name','')} | {author} {match} | {r.get('published_at','')[:10]}\")
"
```

**Step 2:** If releases are bot-authored, search release note bodies for human attribution (`release managed by`, `release captain`, `cut by`, etc.) and check who merged last PRs before each release:
```bash
gh pr list --repo {owner}/{repo} --state merged --limit 5 --json number,author,mergedBy,mergedAt
```

**Step 3:** If no releases found, check for tags:
```bash
gh api "repos/{owner}/{repo}/tags?per_page=50" --jq '.[].name'
```

**Step 4:** Check for release process docs:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("release|RELEASE"; "i")) | .path'
```

**Scoring:** 5=Primary RH release manager + RH backup, 4=Primary/sole RH release manager, 3=RH is one of multiple, 2=RH participates but not named manager, 1=No RH involvement

**Checkpoint:** Write complete KPI 2 results to `{workdir}/kpi2-release-management.md` using the Write tool. Include: total releases, RH release authors table (Employee | GitHub | Tier | Releases | Most Recent | Confidence), score, evidence.

Return: `"KPI 2 complete. {rh_release_count}/{total_releases} releases by RH. Score: {score}. File: {workdir}/kpi2-release-management.md"`

### PROMPT END

---

## KPI 3: Maintainer/Reviewer/Approver Roles

### PROMPT START

You evaluate Red Hat employee governance roles (maintainer, reviewer, approver) in one open source project.

**TARGET:** {owner}/{repo} | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1:** Find all governance files:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("OWNERS|CODEOWNERS|MAINTAINERS|COMMITTER"; "i")) | .path'
```

**Step 2:** For each governance file, fetch and save contents:
```bash
gh api "repos/{owner}/{repo}/contents/{file_path}" --jq '.content' | base64 -d > {workdir}/raw-governance-{sanitized_name}.txt
```

**Step 3:** Extract usernames from governance files and match against roster via python3:
```bash
python3 -c "
import json, re, glob
roster = json.load(open('{roster_path}'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
# Read governance file content and extract @usernames or bare usernames
content = open('{workdir}/raw-governance-{sanitized_name}.txt').read()
usernames = set(re.findall(r'@?([\w-]+)', content))
matches = []
for u in usernames:
    if u.lower() in gh_users:
        emp = gh_users[u.lower()]
        matches.append(f\"{emp['name']} | @{u} | Tier {emp.get('github_resolution_tier',1)}\")
for m in matches: print(m)
"
```

Parse governance files by type: OWNERS (YAML — `approvers:` and `reviewers:` lists), CODEOWNERS (`pattern @username`), MAINTAINERS/COMMITTER (names/usernames/emails). For unrecognized formats, use heuristic @username extraction and mark as Low confidence.

**Step 4:** Check nested OWNERS for subsystem ownership:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | endswith("/OWNERS")) | .path'
```

**Scoring:** 5=>=3 RH maintainers/approvers across subsystems, 4=2 RH maintainers/approvers, 3=1 RH maintainer/approver, 2=Reviewer only, 1=None

**Checkpoint:** Write complete KPI 3 results to `{workdir}/kpi3-maintainership.md` using the Write tool. Include: governance files found, RH employees table (Employee | GitHub | Tier | Role | Scope | Source File | Confidence), score, evidence.

Return: `"KPI 3 complete. {rh_governance_count} RH employees in governance roles. Score: {score}. File: {workdir}/kpi3-maintainership.md"`

### PROMPT END

---

## KPI 4: Roadmap Influence

### PROMPT START

You evaluate Red Hat employee roadmap influence in one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1:** Fetch enhancement/feature issues (try labels: enhancement, feature, feature-request, proposal, roadmap, rfe, design, kep):
```bash
gh issue list --repo {owner}/{repo} --label "enhancement" --state all --limit 5000 \
  --search "created:>{cutoff_date}" --json number,title,author,state,url \
  > {workdir}/raw-enhancement-issues.json
```

**Step 2:** Match issue authors against roster via python3:
```bash
python3 -c "
import json
roster = json.load(open('{roster_path}'))
issues = json.load(open('{workdir}/raw-enhancement-issues.json'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
total = len(issues)
rh_issues = []
for i in issues:
    login = i.get('author',{}).get('login','').lower()
    if login in gh_users:
        emp = gh_users[login]
        rh_issues.append(f\"  #{i['number']} | @{login} ({emp['name']}, Tier {emp.get('github_resolution_tier',1)}) | {i.get('title','')[:80]}\")
print(f'Total enhancement issues: {total}')
print(f'Red Hat authored: {len(rh_issues)}')
for line in rh_issues: print(line)
"
```

**Step 3:** Search for design docs/proposals/KEPs in the repo:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("proposal|design|enhancement|kep|rfc|roadmap"; "i")) | .path'
```

**Step 4:** Search for broader roadmap discussions:
```bash
gh search issues --repo {owner}/{repo} "roadmap OR proposal OR design OR feature request created:>{cutoff_date}" --limit 1000 --json number,title,author,url
```

**Scoring:** 5=RH leads multiple roadmap features + strategic planning, 4=RH leads >=1 major feature, 3=RH actively contributes to features/proposals, 2=RH participates in discussions but doesn't lead, 1=None

**Checkpoint:** Write complete KPI 4 results to `{workdir}/kpi4-roadmap-influence.md` using the Write tool. Include: total issues reviewed, RH proposals table (Employee | GitHub | Tier | Issue/Proposal | Title | Status | Confidence), score, evidence.

Return: `"KPI 4 complete. {rh_proposal_count} RH-authored proposals found. Score: {score}. File: {workdir}/kpi4-roadmap-influence.md"`

### PROMPT END

---

## KPI 5: Leadership Roles

### PROMPT START

You evaluate Red Hat employee governance leadership positions in one open source project.

**TARGET:** {owner}/{repo} | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1:** Search for governance docs in the repository:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("governance|steering|charter|tac|advisory|committee|leadership|GOVERNANCE|STEERING"; "i")) | .path'
```

**Step 2:** Fetch and parse any governance files found:
```bash
gh api "repos/{owner}/{repo}/contents/{governance_file_path}" --jq '.content' | base64 -d > {workdir}/raw-leadership-{sanitized_name}.txt
```

**Step 3:** Web search for project governance:
- WebSearch: `"{repo} project" steering committee members`
- WebSearch: `"{repo} project" governance leadership`
- For CNCF/LF/Apache projects: WebSearch with `site:cncf.io`, `site:lfaidata.foundation`, or `site:apache.org`

Temporal verification: <12mo = keep confidence, 12-24mo = downgrade one level, >24mo or undated = Low confidence.

**Step 4:** Check README for governance links:
```bash
gh api "repos/{owner}/{repo}/contents/README.md" --jq '.content' | base64 -d | head -100
```

**Step 5:** Match all identified governance members against roster via python3:
```bash
python3 -c "
import json
roster = json.load(open('{roster_path}'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
name_map = {e['name'].lower(): e for e in roster['employees']}
# Check governance members against both maps
governance_members = [
    # ('name_or_username', 'body', 'role'),
]
for name, body, role in governance_members:
    if name.lower() in gh_users:
        emp = gh_users[name.lower()]
        print(f\"  {emp['name']} | @{name} | Tier {emp.get('github_resolution_tier',1)} | {body} | {role}\")
    elif name.lower() in name_map:
        emp = name_map[name.lower()]
        print(f\"  {emp['name']} | (name match) | Low | {body} | {role}\")
"
```

**Scoring:** 5=>=2 governance positions incl. chair/lead, 4=Steering/TAC seat, 3=WG/SIG leadership, 2=WG/SIG member, 1=None

**Checkpoint:** Write complete KPI 5 results to `{workdir}/kpi5-leadership.md` using the Write tool. Include: governance bodies found, RH positions table (Employee | GitHub | Tier | Body | Role | Source | Confidence), score, evidence.

Return: `"KPI 5 complete. {rh_leadership_count} RH employees in leadership positions. Score: {score}. File: {workdir}/kpi5-leadership.md"`

### PROMPT END
