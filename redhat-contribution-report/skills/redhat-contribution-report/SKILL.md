---
name: redhat-contribution-report
description: Evaluates Red Hat's contribution to open source projects by identifying employees under a given manager via LDAP and measuring their GitHub contributions, maintainership, governance roles, and roadmap influence. Use when evaluating Red Hat employee contributions, organizational engagement, or open source investment for one or more projects. Supports multiple projects in a single evaluation run.
license: MIT
compatibility: Requires RHEL or Fedora Linux with access to Red Hat internal LDAP (ldap.corp.redhat.com), a valid Kerberos ticket, and authenticated gh CLI.
metadata:
  author: Adam Miller
  email: admiller@redhat.com
  version: "1.0"
allowed-tools: Bash(gh:*) Bash(ldapsearch:*) Bash(klist:*) Bash(git log:*) Bash(git clone:*) Bash(git remote:*) Bash(mkdir:*) Bash(python3:*) Bash(date:*) Read Glob Grep Task WebSearch WebFetch
---

# Red Hat Open Source Contribution Evaluation

Evaluate Red Hat employee contributions to one or more open source projects by:
1. Traversing Red Hat's internal LDAP to find all employees under a given org leader
2. Resolving their GitHub usernames
3. Dispatching parallel sub-agents to evaluate each project across 5 KPIs
4. Generating a consolidated markdown report

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

### Phase 3: GitHub Username Resolution Summary

Review the roster built in Phase 2:
- Employees with GitHub usernames from LDAP are marked as **Tier 1 (High confidence)**
- Employees without GitHub usernames will be resolved by sub-agents in Phase 4 using git log email matching (**Tier 2, Medium confidence**) and GitHub user search (**Tier 3, Low confidence**)

Report the current resolution state to the user before proceeding.

### Phase 4: Parallel Per-Project Research

Read the sub-agent prompt template from `references/RESEARCH-PROMPTS.md`.

Read the scoring rubric from `assets/scoring-rubric.json`.

For each project, prepare the prompt by substituting:
- `{owner}` and `{repo}` with the project's owner and repository name
- `{employee_roster}` with the complete employee roster (formatted as shown in the template)
- `{resolution_coverage_pct}` with the current GitHub username resolution coverage percentage (resolved / total × 100)
- `{total_employees}` with the total number of employees in the roster
- `{resolved_employees}` with the number of employees with resolved GitHub usernames

Include the following ROSTER COVERAGE context block in each sub-agent prompt after the employee roster:

```
ROSTER COVERAGE: {resolved_employees}/{total_employees} employees have resolved GitHub usernames ({resolution_coverage_pct}%).
If coverage is below 70%, add an undercount caveat to all percentage-based KPI calculations noting that
contribution percentages may understate Red Hat involvement due to incomplete username resolution.
```

**Launch one Task sub-agent per project, ALL IN PARALLEL in a single message.** Use `subagent_type: general-purpose`. Each sub-agent evaluates all 5 KPIs for its assigned project.

The 5 KPIs are:
1. **PR/Commit Contributions** — PRs, commits, code contributions authored or co-authored by roster employees
2. **Release Management** — Release managers who are roster employees
3. **Maintainer/Reviewer/Approver Roles** — Roster employees in OWNERS, CODEOWNERS, MAINTAINERS, or similar governance files
4. **Roadmap Influence** — Enhancement proposals, roadmap features, or design docs led by roster employees
5. **Leadership Roles** — TAC, steering committee, advisory board, or other governance body positions held by roster employees

Refer to `references/DATA-SOURCES.md` for the specific `gh` CLI commands each sub-agent should use.

### Phase 5: Result Collection & Merge

Collect the output from each sub-agent. Each sub-agent returns:
- GitHub username resolutions for previously unresolved employees
- Per-employee contribution map (employee name, GitHub username, roles in the project, KPIs contributed to)
- Per-KPI findings with scores, evidence, and confidence levels

#### §5.1 GitHub Username Merge Rules

When multiple sub-agents resolve the same employee to a GitHub username:
- **Same username, same tier:** Accept — no conflict.
- **Same username, different tiers:** Keep the highest-tier (most reliable) resolution. Record both tiers in the Data Quality section.
- **Different usernames, different tiers:** Accept the higher-tier resolution. Discard the lower-tier candidate but note the discrepancy in the Data Quality section.
- **Different usernames, same tier:** Flag as an unresolvable conflict in the Data Quality section. Do not silently pick one — present both candidates to the user for manual verification. Use the candidate with more evidence (e.g., more commits in the target repos) as the primary, but mark confidence as Low.
- **Never silently discard** a resolution. All conflicts and resolution decisions must be documented.

#### §5.2 KPI Result Aggregation

- Keep per-project KPI results **separate** — do not average or merge scores across projects.
- Verify that each sub-agent's assigned score matches the rubric thresholds in `assets/scoring-rubric.json` against the sub-agent's own reported data. If a score appears inconsistent with the data (e.g., score of 4 but data shows < 10% PR contribution), adjust to match the rubric and note the correction.

#### §5.3 Post-Merge Coverage Update

After merging all newly resolved usernames from sub-agents:
- Recalculate the resolution coverage percentage.
- If coverage improved significantly (> 10 percentage points), note this in the Data Quality section.
- If coverage remains below 70%, ensure the undercount caveat appears in the final report.

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

5. Inform the user of the report location and summarize key findings.

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
