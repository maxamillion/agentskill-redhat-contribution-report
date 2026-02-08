# Red Hat Contribution Report - AgentSkill

This repository contains a Claude Code AgentSkill plugin that evaluates Red Hat employee contributions to open source projects.

## Skill Entry Point

`redhat-contribution-report/skills/redhat-contribution-report/SKILL.md`

## How It Works

1. Takes a manager email and list of GitHub projects as input
2. Traverses Red Hat's internal LDAP (via GSSAPI auth) to find all employees in the manager's org
3. Resolves employee GitHub usernames from LDAP attributes
4. Launches parallel sub-agents (one per project) to evaluate 5 KPIs
5. Generates a consolidated markdown report in `reports/`

## Prerequisites

- RHEL or Fedora Linux with Red Hat internal network access (VPN)
- Valid Kerberos ticket (`kinit`)
- `ldapsearch` CLI (`openldap-clients` package)
- `gh` CLI authenticated (`gh auth login`)
- Claude Code with this plugin installed

## Testing

```bash
# Verify prerequisites
klist
ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com -b ou=users,dc=redhat,dc=com '(mail=shuels@redhat.com)' uid cn
gh auth status

# Run the skill
/redhat-contribution-report shuels@redhat.com kubeflow/kubeflow kserve/kserve
```

Reports are saved to `reports/YYYY-MM-DD-redhat-contribution-eval.md`.
