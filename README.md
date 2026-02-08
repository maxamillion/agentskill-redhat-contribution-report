# Red Hat Open Source Contribution Report

A [Claude Code AgentSkill](https://agentskills.io/home) that evaluates Red Hat employee contributions to open source projects.

## What It Does

Given an organizational leader's email address and a list of open source projects, this skill:

1. **Discovers employees** by traversing Red Hat's internal LDAP hierarchy from the specified manager down through all reports
2. **Resolves GitHub identities** from LDAP attributes (`rhatSocialURL`), git commit email matching, and GitHub user search
3. **Evaluates 5 KPIs** per project using parallel sub-agents:
   - **PR/Commit Contributions** — Code authored or co-authored by Red Hat employees
   - **Release Management** — Release managers who are Red Hat employees
   - **Maintainer/Reviewer/Approver Roles** — Red Hat employees in OWNERS, CODEOWNERS, MAINTAINERS files
   - **Roadmap Influence** — Enhancement proposals and features led by Red Hat employees
   - **Leadership Roles** — Governance positions (TAC, Steering Committee, Advisory Board) held by Red Hat employees
4. **Generates a report** with per-employee role identification, per-project KPI scores, cross-project comparison, and confidence tracking

## Prerequisites

- **Operating System:** RHEL or Fedora Linux
- **Network:** Red Hat internal network access (VPN)
- **Kerberos:** Valid TGT (`kinit your-uid@REDHAT.COM`)
- **LDAP Client:** `openldap-clients` package installed
  ```bash
  # Fedora
  sudo dnf install openldap-clients

  # RHEL
  sudo yum install openldap-clients
  ```
- **GitHub CLI:** `gh` authenticated with your GitHub account
  ```bash
  sudo dnf install gh
  gh auth login
  ```
- **Claude Code:** With this plugin installed

## Installation

### From Marketplace

First, add the marketplace source (inside Claude Code):

```
/plugin marketplace add maxamillion/agentskill-redhat-contribution-report
```

Then install the plugin:

```
/plugin install redhat-contribution-report@maxamillion-agentskill-redhat-contribution-report
```

### From Source

Clone this repository and add it as a local plugin (inside Claude Code):

```bash
git clone https://github.com/maxamillion/agentskill-redhat-contribution-report.git
```

```
/plugin marketplace add /path/to/agentskill-redhat-contribution-report
/plugin install redhat-contribution-report@maxamillion-agentskill-redhat-contribution-report
```

## Usage

```
/redhat-contribution-report <manager_email> <project1> [project2] [project3] ...
```

### Examples

Evaluate Red Hat AI Engineering contributions to ML/AI projects:
```
/redhat-contribution-report shuels@redhat.com kubeflow/kubeflow kserve/kserve mlflow/mlflow vllm-project/vllm
```

Evaluate a single project:
```
/redhat-contribution-report manager@redhat.com kubernetes/kubernetes
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `manager_email` | Yes | Email of the org leader whose reports to evaluate (e.g., `shuels@redhat.com`) |
| `project(s)` | Yes (1+) | GitHub repositories in `owner/repo` format |

## Output

Reports are saved to:
```
reports/YYYY-MM-DD-redhat-contribution-eval.md
```

### Report Contents

- **Executive Summary** with overall scores across all projects
- **Employee Roster** with GitHub username resolution coverage
- **Per-Project Sections** containing:
  - Employee contribution table (name, GitHub username, project roles)
  - 5 KPI evaluations with scores (1-5), evidence, and confidence levels
- **Cross-Project Comparison** table
- **Data Quality & Methodology** notes

### Scoring Scale

Each KPI is scored 1-5:

| Score | Label |
|-------|-------|
| 5 | Dominant/Primary presence |
| 4 | Major contributor |
| 3 | Significant contributor |
| 2 | Minor/peripheral involvement |
| 1 | No involvement found |

### Confidence Levels

| Level | Meaning |
|-------|---------|
| High | Data from authoritative sources (LDAP, GitHub API, governance files) |
| Medium | Data from semi-structured sources or email-matched employees |
| Low | Data from web search or name-matched employees |
| Not Found | Could not find data for this metric |

## Architecture

```
SKILL.md (orchestrator)
  ├── Phase 1: Input parsing & prerequisite checks
  ├── Phase 2: LDAP org traversal (GSSAPI auth)
  ├── Phase 3: GitHub username resolution
  ├── Phase 4: Parallel sub-agents (one per project)
  │     ├── KPI 1: PR/Commit analysis (gh CLI)
  │     ├── KPI 2: Release management (gh CLI)
  │     ├── KPI 3: Governance files (OWNERS, CODEOWNERS, etc.)
  │     ├── KPI 4: Roadmap issues & proposals (gh CLI)
  │     └── KPI 5: Leadership roles (governance docs + web search)
  ├── Phase 5: Result collection & merging
  └── Phase 6: Report generation
```

## File Structure

```
redhat-contribution-report/
├── .claude-plugin/
│   └── plugin.json                  # Plugin identity
└── skills/
    └── redhat-contribution-report/
        ├── SKILL.md                 # Main orchestrator
        ├── assets/
        │   └── scoring-rubric.json  # KPI scoring thresholds
        └── references/
            ├── LDAP-GUIDE.md        # LDAP connection & traversal docs
            ├── DATA-SOURCES.md      # gh CLI commands by KPI
            ├── REPORT-TEMPLATE.md   # Output format specification
            └── RESEARCH-PROMPTS.md  # Sub-agent prompt templates
```

## License

MIT
