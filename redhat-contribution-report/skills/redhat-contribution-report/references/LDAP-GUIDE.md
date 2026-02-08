# LDAP Guide - Red Hat Employee Organization Traversal

## Connection Details

- **Server:** `ldap://ldap.corp.redhat.com`
- **Base DN:** `ou=users,dc=redhat,dc=com`
- **Authentication:** GSSAPI (Kerberos) — always use `-Y GSSAPI`, never use simple auth (`-x`)

## Prerequisites

Verify a valid Kerberos TGT exists before any LDAP queries:

```bash
klist
```

If no valid ticket, the user must run `kinit` first.

## Attribute Reference

### Standard Attributes
| Attribute | Description |
|-----------|-------------|
| `uid` | Unique user identifier (Red Hat Kerberos principal) |
| `cn` | Common name (full display name) |
| `mail` | Email address |
| `title` | Job title |
| `manager` | DN of the user's direct manager |
| `ou` | Organizational unit |

### Social/GitHub Attributes
| Attribute | Description |
|-----------|-------------|
| `rhatSocialURL` | Multi-value field containing social URLs. GitHub entries follow the pattern: `Github->https://github.com/<username>` |

### Attribute Discovery

To discover all available attributes for a user (useful for finding GitHub-specific fields):

```bash
ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
  -b ou=users,dc=redhat,dc=com \
  '(uid=<known_uid>)' '*' '+'
```

Look for any attributes containing `github`, `social`, `git`, or `rhat` in their names.

## Organization Traversal Algorithm

### Step 1: Find the Manager

Lookup the manager's LDAP entry using their email address:

```bash
ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
  -b ou=users,dc=redhat,dc=com \
  '(mail=<manager_email>)' \
  dn uid cn mail title rhatSocialURL
```

Record the manager's `uid` from the result.

### Step 2: Find Direct Reports (Iterative BFS)

Use a breadth-first search to recursively find all reports under the manager:

```bash
ldapsearch -LLL -Y GSSAPI -H ldap://ldap.corp.redhat.com \
  -b ou=users,dc=redhat,dc=com \
  '(manager=uid=<manager_uid>,ou=users,dc=redhat,dc=com)' \
  uid cn mail title rhatSocialURL manager
```

For each result returned:
1. Add the employee to the roster
2. Check if this employee also has direct reports by querying with their `uid` as the manager
3. Continue until no new reports are found at any level

### Step 3: Build Employee Roster

For each employee collected, extract:
- `uid` — Red Hat username
- `cn` — Full name
- `mail` — Email address
- `title` — Job title
- `rhatSocialURL` — Parse for GitHub username

## Parsing rhatSocialURL for GitHub Username

The `rhatSocialURL` attribute may contain multiple values. Each value follows the pattern:

```
<Platform>-><URL>
```

To extract GitHub username, look for entries matching:
```
Github->https://github.com/<username>
```

Parse the username from the URL path.

Example LDAP output:
```
rhatSocialURL: Github->https://github.com/janedoe
rhatSocialURL: Twitter->https://twitter.com/janedoe
rhatSocialURL: LinkedIn->https://www.linkedin.com/in/janedoe
```

In this case, the GitHub username is `janedoe`.

## Handling Large Organizations

If the organization tree exceeds 500 employees:
- Warn the user that the analysis scope is very large
- Suggest narrowing the scope to a more specific manager
- Proceed if the user confirms, but note in the report that query volume against GitHub may trigger rate limiting

## Fallback: LDAP Unavailable

If LDAP is unavailable (no Kerberos ticket, network issues, etc.):
1. Warn the user that LDAP is unavailable
2. Offer to proceed with email-only identification using `git log` analysis
3. Search project git history for `@redhat.com` email addresses
4. Mark ALL metrics with reduced confidence ("email-only identification, no org scoping")
5. Note in the report that results are not scoped to any specific organization
