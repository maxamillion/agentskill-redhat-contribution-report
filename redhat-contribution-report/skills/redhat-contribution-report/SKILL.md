---
name: redhat-contribution-report
description: Evaluates Red Hat's contribution to open source projects by identifying employees under a given manager via LDAP and measuring their GitHub contributions, maintainership, governance roles, and roadmap influence. Use when evaluating Red Hat employee contributions, organizational engagement, or open source investment for one or more projects. Supports multiple projects in a single evaluation run.
license: MIT
compatibility: Requires RHEL or Fedora Linux with access to Red Hat internal LDAP (ldap.corp.redhat.com), a valid Kerberos ticket, and authenticated gh CLI.
metadata:
  author: Adam Miller
  email: admiller@redhat.com
  version: "1.0"
allowed-tools: Bash(gh:*) Bash(ldapsearch:*) Bash(klist:*) Bash(git log:*) Bash(git clone:*) Bash(git remote:*) Bash(mkdir:*) Bash(python3:*) Bash(date:*) Bash(rm:*) Bash(grep:*) Read Glob Grep Task WebSearch WebFetch Write
---

# Red Hat Open Source Contribution Evaluation

Evaluate Red Hat employee contributions to one or more open source projects by:
1. Traversing Red Hat's internal LDAP to find all employees under a given org leader
2. Writing the employee roster to a JSON file for sub-agent consumption
3. Centralizing GitHub username resolution via a dedicated sub-agent
4. Dispatching 5 parallel KPI sub-agents per project (one per KPI)
5. Generating a consolidated markdown report from checkpoint files

## Quick Start

```
/redhat-contribution-report shuels@redhat.com kubeflow/kubeflow kserve/kserve mlflow/mlflow vllm-project/vllm
```

## Input Format

```
$ARGUMENTS = <manager_email> <project1> [project2] [project3] ...
```

- **manager_email** (required): Email address of the org leader to scope the LDAP search (e.g., `shuels@redhat.com`)
- **projects** (required, one or more): GitHub repositories in `owner/repo` format. If only a project name is given (e.g., `kubeflow`), attempt to resolve it via `gh search repos`

## Evaluation Workflow

Execute these phases sequentially. Do not skip phases.

### Phase 1: Input Parsing & Prerequisites

1. Parse `$ARGUMENTS` to extract:
   - `manager_email`: The first argument (must contain `@`)
   - `projects`: All remaining arguments

2. If any project lacks an `owner/` prefix, resolve it:
   ```bash
   gh search repos "{project_name}" --limit 5 --json fullName,description,stargazersCount
   ```
   Select the most likely match and confirm with the user.

3. Run prerequisite checks (all must pass before continuing):

   **Kerberos ticket:**
   ```bash
   klist
   ```
   If no valid TGT, stop and tell the user to run `kinit`.

   **LDAP connectivity:**
   ```bash
   ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com -b ou=users,dc=redhat,dc=com '(mail=MANAGER_EMAIL)' uid cn 2>&1 | head -5
   ```
   If this fails, warn the user. Refer to `references/LDAP-GUIDE.md` for the fallback strategy.

   **GitHub CLI authentication:**
   ```bash
   gh auth status
   ```
   If not authenticated, stop and tell the user to run `gh auth login`.

   **Validate each project exists:**
   ```bash
   gh repo view OWNER/REPO --json name,owner,url
   ```
   If a project is not found, remove it from the list and warn the user.

   **Create output directory:**
   ```bash
   mkdir -p reports/
   ```

### Phase 2: LDAP Organization Enumeration

Refer to `references/LDAP-GUIDE.md` for detailed LDAP query patterns and attribute documentation.

All LDAP queries MUST use GSSAPI authentication (`-Y GSSAPI`). Never use simple auth (`-x`).

1. **Find the manager's LDAP entry:**
   ```bash
   ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
     -b ou=users,dc=redhat,dc=com \
     '(mail=MANAGER_EMAIL)' \
     uid cn mail title rhatSocialURL
   ```
   Record the manager's `uid`.

2. **Discover available GitHub-related attributes:**
   Run a broad attribute query on the manager's entry to discover any GitHub-specific fields:
   ```bash
   ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
     -b ou=users,dc=redhat,dc=com \
     '(mail=MANAGER_EMAIL)' '*' '+' 2>/dev/null | grep -i -E 'github|social|git'
   ```
   Note any additional attributes found beyond `rhatSocialURL`.

3. **Recursively find all reports (BFS traversal):**

   Initialize a queue with the manager's `uid`. For each `uid` in the queue:
   ```bash
   ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
     -b ou=users,dc=redhat,dc=com \
     '(manager=uid=CURRENT_UID,ou=users,dc=redhat,dc=com)' \
     uid cn mail title rhatSocialURL
   ```
   - Deduplicate by `uid` — if an employee is already in the roster, skip (avoids duplicates from dotted-line reporting or circular references)
   - Add each new result to the employee roster with a `depth` field tracking the BFS level (manager = 0, direct reports = 1, etc.)
   - Add each result's `uid` to the queue for further traversal
   - Continue until the queue is empty (no more reports found at any level)

4. **Build the employee roster:**
   For each employee, create an entry with:
   - `name` (from `cn`)
   - `uid` (from `uid`)
   - `email` (from `mail`)
   - `title` (from `title`)
   - `github_username` (parsed from `rhatSocialURL` or other discovered attribute, or null)
   - `github_resolution_method` (`ldap` if resolved, `null` if not)

5. **Report roster statistics:**
   - Total employees found
   - Total with GitHub usernames resolved
   - Coverage percentage
   - If coverage < 70%, warn that metrics may undercount Red Hat involvement

   If the org exceeds 500 employees, warn the user that this is a very large scope and ask if they want to continue or narrow the search.

6. **Write the employee roster to a JSON file** for sub-agent consumption:
   ```bash
   mkdir -p reports/tmp
   ```
   Then use the Write tool to save the roster to `reports/tmp/employee-roster.json` with this schema:
   ```json
   {
     "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
     "manager": {"name": "...", "uid": "...", "email": "..."},
     "total_employees": 125,
     "resolved_count": 40,
     "resolution_coverage_pct": 32.0,
     "employees": [
       {
         "name": "Jane Doe",
         "uid": "jdoe",
         "email": "jdoe@redhat.com",
         "title": "Senior Software Engineer",
         "github_username": "janedoe",
         "github_resolution_method": "ldap",
         "github_resolution_tier": 1,
         "depth": 2
       }
     ]
   }
   ```
   - Set `github_resolution_tier` to `1` for LDAP-resolved usernames, `null` for unresolved
   - Set `github_username` to `null` for employees without LDAP resolution
   - This file is the single source of truth for the roster — sub-agents reference it by path and never receive the roster inline

### Phase 3: GitHub Username Resolution Summary

Review the roster JSON written in Phase 2 (`reports/tmp/employee-roster.json`):
- Employees with GitHub usernames from LDAP are marked as **Tier 1 (High confidence)** (`github_resolution_tier: 1`)
- Employees without GitHub usernames (`github_username: null`) will be resolved by the centralized Username Resolution Agent in Phase 3.5

Report the current resolution state to the user before proceeding. Include:
- Total employees, resolved count, coverage percentage
- Note that Phase 3.5 will attempt to resolve remaining employees before KPI evaluation begins

### Phase 3.5: Centralized Username Resolution

Launch a **single** dedicated sub-agent to resolve GitHub usernames for all employees who lack LDAP-resolved usernames. This runs once before KPI evaluation, so all KPI agents benefit from the same resolved roster.

Read the Username Resolution Agent prompt template from `references/RESEARCH-PROMPTS.md`.

Prepare the prompt by substituting:
- `{roster_path}` with `reports/tmp/employee-roster.json`
- `{project_list}` with a comma-separated list of all target projects (e.g., `kubeflow/kubeflow, kserve/kserve`)
- `{owner}` and `{repo}` placeholders in the git history search commands with each project's owner/repo
- `{workdir}` with `reports/tmp`

Launch the sub-agent using `Task` with `subagent_type: general-purpose` and `max_turns: 30`.

**Wait for this agent to complete** before proceeding to Phase 4. The agent will:
1. Read unresolved employees via python3 (never loads full roster into context)
2. Batch-search git history across ALL target projects for `@redhat.com` emails
3. Confirm matches via `gh search commits --author-email`
4. For remaining unresolved (if <20), try `gh search users` with strict acceptance criteria
5. Update `reports/tmp/employee-roster.json` in place with resolutions
6. Write resolution log to `reports/tmp/username-resolutions.md`

After the agent completes, report the updated resolution coverage to the user.

### Phase 4: Parallel Per-Project Research

Read the 5 KPI prompt templates from `references/RESEARCH-PROMPTS.md`.

Read the scoring rubric from `assets/scoring-rubric.json`.

**Create working directories** for each project's intermediate files:
```bash
mkdir -p reports/tmp/{owner}-{repo}/
```
Run this for every project before dispatching sub-agents. These directories hold raw API output and checkpoint files.

**Compute the evaluation window cutoff date** (6 months ago from today):
```bash
cutoff_date=$(date -d '6 months ago' +%Y-%m-%d)
```

For each KPI prompt template, prepare the prompt by substituting:
- `{owner}` and `{repo}` with the project's owner and repository name
- `{workdir}` with the working directory path: `reports/tmp/{owner}-{repo}`
- `{cutoff_date}` with the computed 6-month-ago date in `YYYY-MM-DD` format
- `{roster_path}` with `reports/tmp/employee-roster.json`

**Do NOT substitute `{employee_roster}` or embed the roster inline.** Sub-agents access the roster file via `{roster_path}` inside python3 scripts. The roster is never loaded into agent conversation context.

**Launch 5 Task sub-agents per project, ALL IN PARALLEL in a single message.** Use `subagent_type: general-purpose`. For N projects, this means 5N Task calls in a single message.

| Agent | KPI | Focus | max_turns |
|-------|-----|-------|-----------|
| KPI 1 | PR/Commit Contributions | PRs, commits, code contributions authored or co-authored by roster employees | 12 |
| KPI 2 | Release Management | Release managers who are roster employees | 8 |
| KPI 3 | Maintainer/Reviewer/Approver Roles | Roster employees in OWNERS, CODEOWNERS, MAINTAINERS, or similar governance files | 8 |
| KPI 4 | Roadmap Influence | Enhancement proposals, roadmap features, or design docs led by roster employees | 8 |
| KPI 5 | Leadership Roles | TAC, steering committee, advisory board, or other governance body positions held by roster employees | 8 |

KPI 1 agents get `max_turns: 12` because workflow detection and larger PR fetches for high-volume or non-standard repos require additional API round-trips. All other KPIs use `max_turns: 8`.

Each agent writes its results to a checkpoint file in `{workdir}/` and returns only a 1-line status message. This keeps orchestrator context minimal.

Refer to `references/DATA-SOURCES.md` for the specific `gh` CLI commands each sub-agent should use.

### Phase 5: Result Collection & Merge

Collect the output from all sub-agents. There are **5 sub-agents per project** (5N total for N projects), each returning a 1-line status message. Detailed results are in checkpoint files.

**Step 1:** Read the updated roster from `reports/tmp/employee-roster.json` (updated by the Phase 3.5 Username Resolution Agent).

**Step 2:** Read the username resolution log from `reports/tmp/username-resolutions.md`.

**Step 3:** For each project, read the 5 KPI checkpoint files from `reports/tmp/{owner}-{repo}/`:
- `kpi1-pr-contributions.md` — KPI 1 results
- `kpi2-release-management.md` — KPI 2 results
- `kpi3-maintainership.md` — KPI 3 results
- `kpi4-roadmap-influence.md` — KPI 4 results
- `kpi5-leadership.md` — KPI 5 results

**Step 4: Handle missing checkpoints.** If a checkpoint file does not exist (agent failed or was rate-limited), mark that KPI as "Not evaluated" with score 1 and confidence "Not Found". Note which KPIs were missing in the Data Quality section.

**Step 5: Build per-project Employee Contribution Maps** using python3 to scan checkpoint files:
```bash
python3 -c "
import json, re, os
roster = json.load(open('reports/tmp/employee-roster.json'))
workdir = 'reports/tmp/{owner}-{repo}'
kpi_files = ['kpi1-pr-contributions.md','kpi2-release-management.md','kpi3-maintainership.md','kpi4-roadmap-influence.md','kpi5-leadership.md']
gh_users = {e['github_username'].lower(): e['name'] for e in roster['employees'] if e.get('github_username')}
for i, f in enumerate(kpi_files, 1):
    path = os.path.join(workdir, f)
    if os.path.exists(path):
        content = open(path).read()
        found = [u for u in gh_users if u in content.lower()]
        for u in found:
            print(f'{gh_users[u]} | @{u} | KPI {i}')
    else:
        print(f'KPI {i}: checkpoint missing')
"
```

#### §5.1 KPI Result Aggregation

- Keep per-project KPI results **separate** — do not average or merge scores across projects.
- Verify that each sub-agent's assigned score matches the rubric thresholds in `assets/scoring-rubric.json` against the sub-agent's own reported data. If a score appears inconsistent with the data (e.g., score of 4 but data shows < 10% PR contribution), adjust to match the rubric and note the correction.

#### §5.2 Coverage Verification

After collecting all results:
- Read the final resolution coverage from `reports/tmp/employee-roster.json` (`resolution_coverage_pct` field).
- If coverage is below 70%, ensure the undercount caveat appears in the final report.
- Note the resolution coverage and method breakdown in the Data Quality section.

### Phase 6: Report Generation

Read the report template from `references/REPORT-TEMPLATE.md`.

Generate the final report by:
1. Computing today's date:
   ```bash
   date +%Y-%m-%d
   ```

2. Assembling the report following the template structure:
   - **Executive Summary** with overall scores table
   - **Employee Roster** with coverage statistics and unresolved employees table
   - **Per-Project Sections** — one for each project, each containing:
     - Employee contribution table (name, GitHub username, roles, KPIs)
     - All 5 KPI sections with scores, findings, evidence, and confidence
     - Project score summary table
   - **Cross-Project Comparison** table and cross-project employee presence
   - **Data Quality & Methodology** notes
   - **Sources** list

3. Applying scores using the rubric from `assets/scoring-rubric.json`

4. Writing the report:
   ```bash
   # File path: reports/YYYY-MM-DD-redhat-contribution-eval.md
   ```
   Use the Write tool to save the report.

5. Clean up intermediate files:
   ```bash
   rm -rf reports/tmp/
   ```

6. Inform the user of the report location and summarize key findings.

## Error Handling

- **Kerberos/LDAP failure:** Warn user. Offer email-only fallback (search git history for `@redhat.com`). All metrics marked reduced confidence. No org scoping possible.
- **gh rate limited (403):** Reduce sample sizes by 50%. Note in Data Quality section. If still limited, report partial data.
- **Project not found:** Skip the project. Note in the report.
- **Governance files not found:** Mark KPIs 3 and 5 as low confidence. Use web search as fallback.
- **Org exceeds 500 employees:** Warn user. Suggest narrowing scope. Proceed only with confirmation.
- **Coverage below 70%:** Add warning banner to all contribution metrics in the report.

## Reference Files

- `references/LDAP-GUIDE.md` — LDAP connection, attributes, traversal algorithm, and fallback strategies
- `references/DATA-SOURCES.md` — All `gh` CLI commands organized by KPI with parsing guidance
- `references/REPORT-TEMPLATE.md` — Complete markdown template for the output report
- `references/RESEARCH-PROMPTS.md` — Sub-agent prompt template with variable substitution instructions
- `assets/scoring-rubric.json` — Machine-readable scoring thresholds for all 5 KPIs (1-5 scale)
