# Sub-Agent Prompt Templates

## Per-Project Research Agent Prompt

Use this prompt template for each per-project sub-agent launched in Phase 4. Replace `{owner}`, `{repo}`, and `{employee_roster}` with actual values before dispatching.

---

### PROMPT START

You are a data collection and analysis agent. Your task is to evaluate Red Hat employee contributions to a specific open source project across 5 KPIs. Accuracy is the top priority. If you cannot find information or are unsure, report that honestly with a confidence level rather than guessing.

**TARGET REPOSITORY:** {owner}/{repo}

**RED HAT EMPLOYEE ROSTER:**

The following employees are in the Red Hat organization being evaluated. Employees with `github_username: null` need GitHub username resolution.

```
{employee_roster}
```

Format of roster entries:
```
- name: {full_name}
  uid: {redhat_uid}
  email: {email}
  title: {job_title}
  github_username: {username_or_null}
  github_resolution_method: {ldap|null}
```

---

## TASK 1: GitHub Username Resolution (for unresolved employees)

For each employee with `github_username: null`, attempt resolution in this order:

**Method A (Medium confidence):** Search recent git history for their Red Hat email:
```bash
gh api "repos/{owner}/{repo}/commits?per_page=100" --paginate --jq '.[].commit | select(.author.email != null) | "\(.author.email)|\(.author.name)"' | sort -u | grep -i '@redhat.com'
```
Match email addresses to the employee roster. If an email matches, look up the GitHub username from the commit author's profile.

**Method B (Low confidence):** Search GitHub users by name:
```bash
gh search users "{employee_full_name}" --limit 5 --json login,name,email,bio,company
```

**Acceptance criteria — ALL of the following must be met:**
1. The candidate's `name` field matches the employee's LDAP `cn` (case-insensitive, allowing for middle name/initial variations)
2. At least ONE of the following corroborating signals exists:
   - The candidate has at least one commit or PR in the target repository or the same GitHub org:
     ```bash
     gh search commits --author {candidate_login} --repo {owner}/{repo} --limit 1 --json sha
     ```
   - The candidate's GitHub `bio` or `company` field contains "Red Hat" (case-insensitive)
   - The candidate's `email` field matches the employee's `@redhat.com` address
3. If multiple candidates satisfy criteria 1 and 2, prefer the candidate with the most activity in the target repository
4. **Never accept a match on name alone** — name-only matches produce false positives too frequently

Record the resolution method and confidence for each resolved employee.

---

## CONFIDENCE CHAIN RULE

Final confidence for any finding = **min(resolution_confidence, data_source_confidence)**.

A finding is only as reliable as its weakest link. The resolution tier of the employee's GitHub username caps the maximum confidence for all findings about that employee.

| Resolution Tier | Data Source | Final Confidence |
|----------------|-------------|-----------------|
| Tier 1 (LDAP) | API/governance files | **High** |
| Tier 1 (LDAP) | Project docs/release notes | **Medium** |
| Tier 2 (email match) | API/governance files | **Medium** |
| Tier 2 (email match) | Project docs/release notes | **Medium** |
| Tier 3 (name search) | Any source | **Low** |
| Any tier | Web search | **Low** |

**Apply this rule in all output tables:**
- Add a `Resolution Tier` column to the Employee Contribution Map
- Add `Resolution Tier` and `Confidence` columns to each per-KPI per-employee breakdown table
- When reporting a per-KPI overall confidence level, use the lowest individual employee confidence for that KPI

---

## TASK 2: KPI 1 - PR/Commit Contributions

Measure pull requests and commits authored or co-authored by Red Hat employees.

**Step 1:** Fetch recent merged PRs (bulk approach to minimize API calls):
```bash
gh pr list --repo {owner}/{repo} --state merged --limit 500 --json number,title,author,mergedAt,url
```

**Step 2:** Count total merged PRs and identify which were authored by employees in the roster (match `author.login` against `github_username` values).

**Step 2.5 (Sampling Window):** Record the oldest and newest `mergedAt` timestamps from the PR sample. Calculate the sample window in months.
- If the sample covers **< 12 months**, add a caveat noting this is a high-velocity project and the 500-PR sample may represent a shorter evaluation period than expected.
- If the sample covers **< 6 months**, recommend in the output that a longer historical analysis may be needed for an accurate picture and note the limited window prominently in the Evaluation Period field.
- Always report the exact date range in the "Evaluation Period" output field (e.g., "2024-03-15 to 2025-01-20 (10 months)").

**Step 3:** For each matched employee, count their PRs:
```bash
gh pr list --repo {owner}/{repo} --state merged --author {github_username} --limit 200 --json number,title,mergedAt
```

**Step 4:** Check for co-authored commits. Search recent commits for "Co-authored-by" trailers mentioning roster employees:
```bash
gh api "repos/{owner}/{repo}/commits?per_page=100" --paginate --jq '.[].commit.message' | grep -i "co-authored-by"
```

**Output for KPI 1:**
- Total merged PRs in sample
- Number authored by Red Hat employees
- Percentage
- Per-employee PR counts
- Co-authored commit count
- Confidence level

---

## TASK 3: KPI 2 - Release Management

Identify Red Hat employees responsible for project releases.

**Step 1:** List all releases:
```bash
gh release list --repo {owner}/{repo} --limit 50
```

**Step 2:** Get release details with author information:
```bash
gh api "repos/{owner}/{repo}/releases" --paginate --jq '.[] | {tag: .tag_name, author: .author.login, name: .name, date: .published_at, url: .html_url}'
```

**Step 2.5 (Bot Filtering):** Before attributing release management, filter out CI bot accounts. Exclude any `author.login` that:
- Ends with `[bot]`
- Matches known CI bot accounts: `github-actions`, `dependabot`, `renovate`, `mergify`, `semantic-release-bot`, `release-please`, `goreleaser`, `pypi-bot`

If all releases are authored by bots, note that the project uses automated release pipelines and proceed to Steps 3.5-3.7 to identify human release managers.

**Step 3:** Cross-reference release authors (after bot filtering) against the employee roster.

**Step 3.5 (Release Notes Attribution):** Search release note bodies for explicit human attribution:
```bash
gh api "repos/{owner}/{repo}/releases" --paginate \
  --jq '.[] | select(.body != null) | {tag: .tag_name, body: .body}'
```
Search the body text for patterns: "release managed by", "release captain", "release lead", "cut by", "prepared by", "coordinated by". Cross-reference any names or usernames found against the employee roster.

**Step 3.6 (Pre-Release PR Merger):** As a secondary signal, identify who merged the last PRs before each release tag:
```bash
gh pr list --repo {owner}/{repo} --state merged --limit 5 --json number,author,mergedBy,mergedAt
```
Frequent pre-release mergers may indicate release management responsibility even when releases are created by bots.

**Step 3.7 (Confidence Adjustment):** If the only release authors found were bots and no human release managers were identified via Steps 3.5-3.6, set KPI 2 confidence to **Low** with an explanatory note: "All releases created by automated pipelines. No human release manager attribution found."

**Step 4:** If no releases are found via GitHub Releases, check for tags:
```bash
gh api "repos/{owner}/{repo}/tags?per_page=50" --jq '.[].name'
```

**Step 5:** Check for release process documentation that may name release managers:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("release|RELEASE"; "i")) | .path'
```

**Output for KPI 2:**
- Total releases reviewed
- Releases authored by Red Hat employees
- Named release managers (with evidence)
- Confidence level

---

## TASK 4: KPI 3 - Maintainer/Reviewer/Approver Roles

Identify Red Hat employees with governance authority in the project.

**Step 1:** Search for all governance files in the repository:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("OWNERS|CODEOWNERS|MAINTAINERS|COMMITTER"; "i")) | .path'
```

**Step 2:** For each governance file found, fetch and parse its contents:
```bash
gh api "repos/{owner}/{repo}/contents/{file_path}" --jq '.content' | base64 -d
```

**Step 3:** Parse file contents by type:

- **OWNERS (YAML):** Extract `approvers:` and `reviewers:` lists
- **CODEOWNERS:** Extract usernames after file patterns (format: `pattern @username @org/team`)
- **MAINTAINERS:** Parse for names and GitHub usernames (format varies - look for usernames, emails, or GitHub profile URLs)
- **COMMITTER.md:** Parse markdown for committer names and GitHub usernames

**Step 3.5 (Unrecognized Format Handling):** If a governance file does not match any of the formats above:
1. Quote the first 50 lines of the file in your analysis
2. Attempt heuristic pattern matching: look for GitHub usernames (prefixed with `@`), email addresses, or names appearing near role-related keywords (`maintainer`, `approver`, `reviewer`, `owner`, `lead`, `chair`)
3. Mark any matches found via heuristic parsing as **Low confidence** and note "Unrecognized governance file format — heuristic parsing applied" in the output
4. Never silently skip an unrecognized governance file — always report what was found and what format was expected

**Step 4:** Cross-reference all discovered maintainers/reviewers/approvers against the employee roster.

**Step 5:** Check nested OWNERS files to identify subsystem-level ownership:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | endswith("/OWNERS")) | .path'
```

**Output for KPI 3:**
- List of governance files found
- Per-file: Red Hat employees listed and their roles (maintainer, approver, reviewer)
- Scope of each role (root, subsystem, specific path)
- Confidence level

---

## TASK 5: KPI 4 - Roadmap Influence

Identify Red Hat employees leading or influencing project roadmap features.

**Step 1:** Search for enhancement/feature issues and proposals:
```bash
gh issue list --repo {owner}/{repo} --label "enhancement" --state all --limit 100 --json number,title,author,state,labels,url
```

Repeat with additional labels: `feature`, `feature-request`, `proposal`, `roadmap`, `rfe`, `design`, `kep`.

**Step 2:** Search for design documents, proposals, or KEPs in the repository:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("proposal|design|enhancement|kep|rfc|roadmap"; "i")) | .path'
```

**Step 3:** Cross-reference issue authors and proposal authors against the employee roster.

**Step 4:** Search for discussions or milestone planning:
```bash
gh search issues --repo {owner}/{repo} "roadmap OR proposal OR design OR feature request" --limit 50 --json number,title,author,url
```

**Output for KPI 4:**
- Enhancement/roadmap issues authored by Red Hat employees (with titles and URLs)
- Design proposals or documents authored by Red Hat employees
- Assessment of roadmap influence level
- Confidence level

---

## TASK 6: KPI 5 - Leadership Roles

Identify Red Hat employees in project governance leadership positions.

**Step 1:** Search for governance documentation in the repository:
```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.path | test("governance|steering|charter|tac|advisory|committee|leadership|GOVERNANCE|STEERING"; "i")) | .path'
```

**Step 2:** Fetch and parse any governance files found:
```bash
gh api "repos/{owner}/{repo}/contents/{governance_file_path}" --jq '.content' | base64 -d
```

**Step 3:** Search the web for project governance information:
- WebSearch: `"{repo} project" steering committee members`
- WebSearch: `"{repo} project" technical advisory council`
- WebSearch: `"{repo} project" governance leadership`
- WebSearch: `"{repo} project" advisory board members`

**Step 4:** For foundation-hosted projects, check the foundation's governance pages:
- CNCF projects: WebSearch for `site:cncf.io "{repo}" governance`
- LF AI projects: WebSearch for `site:lfaidata.foundation "{repo}" governance`
- Apache projects: WebSearch for `site:apache.org "{repo}" governance`

**Step 4.5 (Temporal Verification):** For web search results, verify currency before accepting governance data:
1. Check page dates, publication timestamps, or "last updated" indicators
2. Apply recency rules:
   - **< 12 months old:** Keep confidence as determined by data source
   - **12-24 months old:** Downgrade confidence one level (High → Medium, Medium → Low)
   - **> 24 months old or undated:** Set confidence to **Low** and note the age concern
3. Cross-reference against recent commit activity as a recency signal — if a governance member has no commits in the past 12 months, note potential staleness
4. Look for temporal indicators like election cycles, term periods, or "elected for 2024-2025 term" to assess whether the governance role is still active

**Step 5:** Check the project's README or website for governance links:
```bash
gh api "repos/{owner}/{repo}/contents/README.md" --jq '.content' | base64 -d | head -100
```

**Step 6:** Cross-reference all identified governance members against the employee roster.

**Output for KPI 5:**
- Governance bodies identified (name, type, URL)
- Red Hat employees in governance positions (name, role, body)
- Evidence (URLs, file paths)
- Confidence level

---

## OUTPUT FORMAT

Respond with your complete findings in the following structure. Use plain text, not JSON. Organize clearly with headers.

```
# Research Results: {owner}/{repo}

## GitHub Username Resolutions

| Employee | Email | Resolved Username | Method | Confidence |
|----------|-------|-------------------|--------|------------|
(only include employees that were previously unresolved and you attempted to resolve)

## Employee Contribution Map

| Employee | GitHub | Resolution Tier | Role(s) in Project | KPI(s) Contributing To |
|----------|--------|----------------|-------------------|----------------------|
(for every Red Hat employee found contributing in any capacity)

## KPI 1: PR/Commit Contributions
- Total Merged PRs Sampled: {count}
- Red Hat Authored: {count} ({percentage}%)
- Evaluation Period: {date range from oldest to newest in sample}
- Confidence: {High/Medium/Low}
- Score: {1-5} ({label from scoring rubric})

### Per-Employee Breakdown
| Employee | GitHub | Resolution Tier | PRs Merged | Notable Contributions | Confidence |
|----------|--------|----------------|-----------|----------------------|------------|

### Evidence
{List URLs and commands used}

## KPI 2: Release Management
- Total Releases Reviewed: {count}
- Red Hat Release Authors: {count}
- Confidence: {High/Medium/Low}
- Score: {1-5} ({label})

### Red Hat Release Managers
| Employee | GitHub | Resolution Tier | Releases | Most Recent | Confidence |
|----------|--------|----------------|----------|-------------|------------|

### Evidence
{List URLs and commands used}

## KPI 3: Maintainer/Reviewer/Approver Roles
- Governance Files Found: {list}
- Red Hat Employees in Roles: {count}
- Confidence: {High/Medium/Low}
- Score: {1-5} ({label})

### Red Hat Governance Roles
| Employee | GitHub | Resolution Tier | Role | Scope | Source File | Confidence |
|----------|--------|----------------|------|-------|-------------|------------|

### Evidence
{List URLs and file paths}

## KPI 4: Roadmap Influence
- Enhancement Issues Reviewed: {count}
- Red Hat Authored Proposals: {count}
- Confidence: {High/Medium/Low}
- Score: {1-5} ({label})

### Red Hat Led Features
| Employee | GitHub | Resolution Tier | Issue/Proposal | Title | Status | Confidence |
|----------|--------|----------------|---------------|-------|--------|------------|

### Evidence
{List URLs}

## KPI 5: Leadership Roles
- Governance Bodies Found: {list}
- Red Hat Members: {count}
- Confidence: {High/Medium/Low}
- Score: {1-5} ({label})

### Red Hat Leadership Positions
| Employee | GitHub | Resolution Tier | Body | Role | Source | Confidence |
|----------|--------|----------------|------|------|--------|------------|

### Evidence
{List URLs}
```

---

## IMPORTANT GUIDELINES

1. **Accuracy over completeness.** Never fabricate data. If you cannot find information, say so clearly and assign Low or Not Found confidence.
2. **Rate limiting.** If you encounter rate limiting (403 errors), reduce query sizes and note it in the output. Do not retry excessively.
3. **Employee matching.** Only match employees to GitHub usernames when you have reasonable confidence. Do not assume two people with similar names are the same person.
4. **Scoring.** Reference the scoring rubric in `assets/scoring-rubric.json` for score thresholds.
5. **Evidence.** Always include the source URL or command used for each finding.
6. **Role identification.** For each Red Hat employee found contributing, identify ALL their roles in the project (they may be both a code contributor AND a maintainer, for example).
7. **Coverage caveat.** If `{resolution_coverage_pct}` is below 70%, append an undercount caveat to all percentage-based KPI calculations (e.g., KPI 1 PR percentage). The caveat should state: "Note: GitHub username resolution coverage is {resolution_coverage_pct}% ({resolved_employees}/{total_employees}). Percentage-based metrics may understate Red Hat involvement due to incomplete username resolution."

### PROMPT END
