# Sub-Agent Prompt Templates

Six prompt templates: one **Username Resolution Agent** (centralized, runs once) and five **KPI Agents** (one per KPI per project, all launched in parallel). Replace `{owner}`, `{repo}`, `{cutoff_date}`, `{workdir}`, `{roster_path}`, and `{assets_dir}` with actual values before dispatching.

**Key invariants for all agents:**
- `{roster_path}` appears ONLY inside `python3` script arguments or `python3 -c` strings — never as an argument to the Read tool
- Agents return a 1-line status message; all detailed results go to checkpoint files
- Confidence = min(resolution_tier, data_source). Tier1+API=High, Tier2+API=Medium, Tier3+any=Low
- Never fabricate data. Report gaps honestly with Low or Not Found confidence
- If 403 rate-limited, reduce query sizes and note it. Do not retry excessively

---

## Username Resolution Agent

### PROMPT START

You resolve GitHub usernames for Red Hat employees who lack LDAP-resolved usernames.

**ROSTER:** {roster_path} | **PROJECTS:** {project_list} | **WORKDIR:** {workdir}

**Step 1 — Batch resolve:**
```bash
python3 {assets_dir}/username-batch-resolve.py --roster {roster_path} --projects "{project_list}" --workdir {workdir}
```

**Step 2 — Write resolution log** to `{workdir}/username-resolutions.md` using the Write tool. Include a table of resolved employees (Name | UID | GitHub | Method | Tier) and list any still-unresolved employees.

Return: `"Username resolution complete. {resolved}/{total} resolved ({pct}%). File: {workdir}/username-resolutions.md"`

### PROMPT END

---

## KPI 1: PR/Commit Contributions

### PROMPT START

You evaluate Red Hat employee PR/commit contributions for one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1 — Workflow detection:**
```bash
python3 {assets_dir}/kpi1-workflow-detect.py --owner {owner} --repo {repo} --cutoff {cutoff_date}
```

**Step 2 — Fetch PRs** based on workflow type from Step 1:
- `WORKFLOW=non-standard`: Fetch closed + merged PRs:
  ```bash
  gh pr list --repo {owner}/{repo} --state closed --limit 5000 --json number,author,closedAt > {workdir}/raw-prs.json
  gh pr list --repo {owner}/{repo} --state merged --limit 5000 --json number,author,mergedAt > {workdir}/raw-merged-prs.json
  ```
- `WORKFLOW=high-volume` or `WORKFLOW=standard`:
  ```bash
  gh pr list --repo {owner}/{repo} --state merged --limit 5000 --json number,author,mergedAt > {workdir}/raw-prs.json
  ```

**Step 3 — Analyze and verify:**
```bash
python3 {assets_dir}/kpi1-pr-analysis.py --owner {owner} --repo {repo} --workdir {workdir} --roster {roster_path} --cutoff {cutoff_date}
```

**Step 4 — Co-authored commits:**
```bash
gh api "repos/{owner}/{repo}/commits?per_page=100&since={cutoff_date}T00:00:00Z" --paginate --jq '.[].commit.message' > {workdir}/raw-commit-messages.txt
grep -i "co-authored-by" {workdir}/raw-commit-messages.txt 2>/dev/null | sort | uniq -c | sort -rn | head -20
```

Match any co-author names/emails against the roster. Note co-authored contributions separately.

**Scoring:** 5=>=30%, 4=20-29%, 3=10-19%, 2=1-9%, 1=0%

**Checkpoint:** Write KPI 1 results to `{workdir}/kpi1-pr-contributions.md` using the Write tool. Include: total PRs, RH count/pct, per-employee table (Employee | GitHub | Tier | PRs | Confidence), score, workflow type, and any workflow notes.

Return: `"KPI 1 complete. {rh_count}/{total} PRs ({pct}%). Score: {score}. File: {workdir}/kpi1-pr-contributions.md"`

### PROMPT END

---

## KPI 2: Release Management

### PROMPT START

You evaluate Red Hat employee involvement in release management for one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1 — Fetch releases and match roster:**
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

**Scoring:** 5=Primary RH release manager + RH backup, 4=Primary/sole RH release manager, 3=RH is one of multiple, 2=RH participates but not named manager, 1=No RH involvement

**Checkpoint:** Write KPI 2 results to `{workdir}/kpi2-release-management.md` using the Write tool. Include: total releases, RH release authors table (Employee | GitHub | Tier | Releases | Most Recent | Confidence), score, evidence.

Return: `"KPI 2 complete. {rh_release_count}/{total_releases} releases by RH. Score: {score}. File: {workdir}/kpi2-release-management.md"`

### PROMPT END

---

## KPI 3: Maintainer/Reviewer/Approver Roles

### PROMPT START

You evaluate Red Hat employee governance roles (maintainer, reviewer, approver) in one open source project.

**TARGET:** {owner}/{repo} | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1 — Scan all governance files and match roster:**
```bash
python3 {assets_dir}/governance-file-scanner.py --owner {owner} --repo {repo} --workdir {workdir} --roster {roster_path}
```

**Step 2 — Refine role classification:** Read `{workdir}/governance-matches.json` and any raw governance files in `{workdir}/raw-governance-*.txt` to refine role classifications. For OWNERS files (YAML), distinguish `approvers:` from `reviewers:` lists. For CODEOWNERS, note path patterns indicating subsystem scope.

**Scoring:** 5=>=3 RH maintainers/approvers across subsystems, 4=2 RH maintainers/approvers, 3=1 RH maintainer/approver, 2=Reviewer only, 1=None

**Checkpoint:** Write KPI 3 results to `{workdir}/kpi3-maintainership.md` using the Write tool. Include: governance files found, RH employees table (Employee | GitHub | Tier | Role | Scope | Source File | Confidence), score, evidence.

Return: `"KPI 3 complete. {rh_governance_count} RH employees in governance roles. Score: {score}. File: {workdir}/kpi3-maintainership.md"`

### PROMPT END

---

## KPI 4: Roadmap Influence

### PROMPT START

You evaluate Red Hat employee roadmap influence in one open source project.

**TARGET:** {owner}/{repo} | **WINDOW:** {cutoff_date} to present | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1 — Fetch enhancement issues** (try labels: enhancement, feature, feature-request, proposal, roadmap, rfe, design, kep):
```bash
gh issue list --repo {owner}/{repo} --label "enhancement" --state all --limit 500 \
  --search "created:>{cutoff_date}" --json number,title,author,state,url \
  > {workdir}/raw-enhancement-issues.json
```

**Step 2 — Match issue authors against roster:**
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
        rh_issues.append(f\"  #{i['number']} | @{login} ({emp['name']}, T{emp.get('github_resolution_tier',1)}) | {i.get('title','')[:80]}\")
print(f'Total enhancement issues: {total}')
print(f'Red Hat authored: {len(rh_issues)}')
for line in rh_issues: print(line)
"
```

**Step 3 — Search for design docs/proposals in the repo:**
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("proposal|design|enhancement|kep|rfc|roadmap"; "i")) | .path'
```

**Step 4 — Broader roadmap discussions:**
```bash
gh search issues --repo {owner}/{repo} "roadmap OR proposal OR design OR feature request created:>{cutoff_date}" --limit 200 --json number,title,author,url > {workdir}/raw-roadmap-search.json
python3 -c "
import json
roster = json.load(open('{roster_path}'))
results = json.load(open('{workdir}/raw-roadmap-search.json'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
rh = [r for r in results if r.get('author',{}).get('login','').lower() in gh_users]
print(f'Roadmap search: {len(results)} total, {len(rh)} RH-authored')
for r in rh:
    print(f\"  #{r['number']} | @{r['author']['login']} | {r.get('title','')[:80]}\")
"
```

**Scoring:** 5=RH leads multiple roadmap features + strategic planning, 4=RH leads >=1 major feature, 3=RH actively contributes to features/proposals, 2=RH participates in discussions but doesn't lead, 1=None

**Checkpoint:** Write KPI 4 results to `{workdir}/kpi4-roadmap-influence.md` using the Write tool. Include: total issues reviewed, RH proposals table (Employee | GitHub | Tier | Issue/Proposal | Title | Status | Confidence), score, evidence.

Return: `"KPI 4 complete. {rh_proposal_count} RH-authored proposals found. Score: {score}. File: {workdir}/kpi4-roadmap-influence.md"`

### PROMPT END

---

## KPI 5: Leadership Roles

### PROMPT START

You evaluate Red Hat employee governance leadership positions in one open source project.

**TARGET:** {owner}/{repo} | **WORKDIR:** {workdir} | **ROSTER:** {roster_path}

**Step 1 — Scan governance/leadership files:**
```bash
python3 {assets_dir}/governance-file-scanner.py --owner {owner} --repo {repo} --workdir {workdir} --roster {roster_path} --pattern "governance|steering|charter|tac|advisory|committee|leadership|GOVERNANCE|STEERING"
```

**Step 2 — Web search for project governance** (max 2 searches):
- WebSearch: `"{repo} project" steering committee governance members`
- For CNCF/LF/Apache projects: WebSearch with the relevant foundation domain

Temporal verification: <12mo=keep confidence, 12-24mo=downgrade one level, >24mo or undated=Low.

**Step 3 — Match governance members from web results against roster:**
```bash
python3 -c "
import json
roster = json.load(open('{roster_path}'))
gh_users = {e['github_username'].lower(): e for e in roster['employees'] if e.get('github_username')}
name_map = {e['name'].lower(): e for e in roster['employees']}
governance_members = [
    # Fill in from web search results: ('name_or_username', 'body_name', 'role'),
]
for name, body, role in governance_members:
    if name.lower() in gh_users:
        emp = gh_users[name.lower()]
        print(f\"  {emp['name']} | @{name} | T{emp.get('github_resolution_tier',1)} | {body} | {role}\")
    elif name.lower() in name_map:
        emp = name_map[name.lower()]
        print(f\"  {emp['name']} | (name match) | Low | {body} | {role}\")
"
```

**Scoring:** 5=>=2 governance positions incl. chair/lead, 4=Steering/TAC seat, 3=WG/SIG leadership, 2=WG/SIG member, 1=None

**Checkpoint:** Write KPI 5 results to `{workdir}/kpi5-leadership.md` using the Write tool. Include: governance bodies found, RH positions table (Employee | GitHub | Tier | Body | Role | Source | Confidence), score, evidence.

Return: `"KPI 5 complete. {rh_leadership_count} RH employees in leadership positions. Score: {score}. File: {workdir}/kpi5-leadership.md"`

### PROMPT END
