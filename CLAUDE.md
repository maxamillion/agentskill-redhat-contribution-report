# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code AgentSkill marketplace plugin that evaluates Red Hat employee contributions to open source projects. It uses LDAP for org discovery, `gh` CLI for GitHub data, and parallel sub-agents for per-project analysis.

## Skill Entry Point

`redhat-contribution-report/skills/redhat-contribution-report/SKILL.md`

## Architecture

The skill runs a 7-phase sequential workflow orchestrated by SKILL.md:

1. **Input parsing** — manager email + project list from `$ARGUMENTS`
2. **LDAP traversal** — BFS walk of Red Hat's LDAP tree using GSSAPI auth (`-Y GSSAPI`, never `-x`); writes roster to `reports/tmp/employee-roster.json`
3. **Resolution summary** — report LDAP-resolved (Tier 1) vs unresolved employees
3.5. **Centralized username resolution** — single dedicated sub-agent resolves GitHub usernames across all target projects; updates roster JSON in place
4. **Parallel KPI agents** — 5 `Task` sub-agents per project (one per KPI), all 5N launched in a single message with `max_turns: 8`
5. **Result collection** — read 5 checkpoint files per project from `reports/tmp/{owner}-{repo}/`; no inline roster merging needed
6. **Report generation** — markdown report to `reports/YYYY-MM-DD-redhat-contribution-eval.md`

Sub-agents evaluate 5 KPIs per project: PR contributions, release management, maintainership, roadmap influence, and governance leadership roles. The employee roster is externalized to a JSON file (`reports/tmp/employee-roster.json`) — sub-agents access it via `{roster_path}` inside python3 scripts, keeping it out of agent conversation context. Each agent returns a 1-line status and writes detailed results to checkpoint files. Prompt templates are in `references/RESEARCH-PROMPTS.md` with `{owner}`, `{repo}`, and `{roster_path}` placeholders.

## Key Files

| File | Role |
|------|------|
| `SKILL.md` | Orchestrator — phases, LDAP queries, sub-agent dispatch |
| `references/RESEARCH-PROMPTS.md` | Sub-agent prompt template (all 5 KPIs + output schema) |
| `references/LDAP-GUIDE.md` | LDAP server, attributes, traversal algorithm |
| `references/DATA-SOURCES.md` | `gh` CLI commands by KPI |
| `references/REPORT-TEMPLATE.md` | Output format with per-employee role tables |
| `assets/scoring-rubric.json` | 1-5 scoring thresholds and confidence level definitions |

## Plugin Structure

This is a marketplace-distributable plugin. The manifests are:
- `.claude-plugin/marketplace.json` — marketplace registration
- `redhat-contribution-report/.claude-plugin/plugin.json` — plugin identity

## Prerequisites for Testing

Requires Red Hat internal network (VPN), valid Kerberos ticket (`kinit`), `openldap-clients` package, and authenticated `gh` CLI.

```bash
klist
ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com -b ou=users,dc=redhat,dc=com '(mail=shuels@redhat.com)' uid cn
gh auth status
/redhat-contribution-report shuels@redhat.com kubeflow/kubeflow kserve/kserve
```

## Constraints

- LDAP queries must always use GSSAPI auth (`-Y GSSAPI`), never simple auth (`-x`)
- GitHub data collection must use `gh` CLI only — no raw curl/API calls, no API keys
- Reports must identify each contributing employee by name, GitHub username, and their specific role(s) in the project
- Every finding must include a confidence level (High/Medium/Low/Not Found)
- Accuracy is the top priority — report gaps honestly rather than guessing
