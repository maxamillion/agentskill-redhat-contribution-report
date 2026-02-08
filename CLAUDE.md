# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code AgentSkill marketplace plugin that evaluates Red Hat employee contributions to open source projects. It uses LDAP for org discovery, `gh` CLI for GitHub data, and parallel sub-agents for per-project analysis.

## Skill Entry Point

`redhat-contribution-report/skills/redhat-contribution-report/SKILL.md`

## Architecture

The skill runs a 6-phase sequential workflow orchestrated by SKILL.md:

1. **Input parsing** — manager email + project list from `$ARGUMENTS`
2. **LDAP traversal** — BFS walk of Red Hat's LDAP tree using GSSAPI auth (`-Y GSSAPI`, never `-x`)
3. **GitHub username resolution** — 3-tier: LDAP `rhatSocialURL` > git log email match > `gh search users`
4. **Parallel sub-agents** — one `Task` (subagent_type: `general-purpose`) per project, all launched in a single message
5. **Result collection** — merge sub-agent findings and GitHub username resolutions
6. **Report generation** — markdown report to `reports/YYYY-MM-DD-redhat-contribution-eval.md`

Sub-agents evaluate 5 KPIs per project: PR contributions, release management, maintainership, roadmap influence, and governance leadership roles. The prompt template is in `references/RESEARCH-PROMPTS.md` with `{owner}`, `{repo}`, and `{employee_roster}` placeholders.

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
