# Data Sources Reference - gh CLI Commands by KPI

All GitHub data collection uses the `gh` CLI tool exclusively. Never use raw API calls with curl or direct HTTP requests to avoid authentication token management.

## KPI 1: PR/Commit Contributions

### Bulk PR List (Preferred - avoids per-author rate limit pressure)

Fetch all recent merged PRs for a repository and filter locally:

```bash
gh pr list --repo {owner}/{repo} --state merged --limit 500 \
  --json number,title,author,mergedAt,url
```

Cross-reference the `author.login` field against the employee roster GitHub usernames.

### Per-Author PR Search (Use sparingly)

```bash
gh search prs --author {github_username} --repo {owner}/{repo} --merged --limit 100 \
  --json number,title,repository,updatedAt,url
```

### Commit Search by Author

```bash
gh search commits --author {github_username} --repo {owner}/{repo} --limit 100 \
  --json sha,commit,repository,url
```

### Co-authored Commits

Search for co-authored-by trailers in commit messages:

```bash
gh api repos/{owner}/{repo}/commits --paginate --jq '.[].commit.message' | \
  grep -i "co-authored-by.*{employee_name_or_email}"
```

### Git Log Email Matching (Fallback for unresolved employees)

Clone the repo (shallow) and search by email domain:

```bash
git clone --depth 100 --filter=blob:none https://github.com/{owner}/{repo}.git /tmp/{repo}
git -C /tmp/{repo} log --all --format='%ae|%an|%H|%s' | grep -i '@redhat.com'
```

## KPI 2: Release Management

### List All Releases

```bash
gh api repos/{owner}/{repo}/releases --paginate \
  --jq '.[] | {tag: .tag_name, author: .author.login, name: .name, date: .published_at, url: .html_url}'
```

### Release Tags with Authors

```bash
gh release list --repo {owner}/{repo} --limit 50 \
  --json tagName,author,publishedAt,isLatest
```

Cross-reference `author.login` with employee roster to identify Red Hat release managers.

### Release Notes Content

Check release notes for release manager acknowledgments:

```bash
gh release view {tag_name} --repo {owner}/{repo} --json body,author
```

## KPI 3: Maintainer/Reviewer/Approver Roles

### Kubernetes-style OWNERS Files

```bash
gh api repos/{owner}/{repo}/contents/OWNERS --jq '.content' | base64 -d
```

Also check subdirectories for nested OWNERS files:

```bash
gh api repos/{owner}/{repo}/git/trees/HEAD --jq '.tree[] | select(.path | endswith("OWNERS")) | .path'
```

Parse the YAML for `approvers:` and `reviewers:` lists.

### GitHub CODEOWNERS

```bash
gh api repos/{owner}/{repo}/contents/.github/CODEOWNERS --jq '.content' | base64 -d
```

If not found at `.github/CODEOWNERS`, also check:
- `CODEOWNERS` (repo root)
- `docs/CODEOWNERS`

### MAINTAINERS File

```bash
gh api repos/{owner}/{repo}/contents/MAINTAINERS --jq '.content' | base64 -d
```

### COMMITTER.md (MLflow style)

```bash
gh api repos/{owner}/{repo}/contents/COMMITTER.md --jq '.content' | base64 -d
```

### Contributors with Write Access

```bash
gh api repos/{owner}/{repo}/contributors --paginate --jq '.[].login'
```

Note: This shows contributors by commit count, not necessarily write access. Write access is not publicly visible via API.

## KPI 4: Roadmap Influence

### Enhancement/Feature Issues

```bash
gh issue list --repo {owner}/{repo} --label "enhancement" --state all --limit 100 \
  --json number,title,author,state,labels,url
```

Try multiple label variants:
- `enhancement`
- `feature`
- `feature-request`
- `proposal`
- `roadmap`
- `rfe`
- `design`

### Enhancement Proposals / Design Docs

Search for proposal or design documents in the repository:

```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" \
  --jq '.tree[] | select(.path | test("proposal|design|enhancement|kep|rfc"; "i")) | .path'
```

### Issue Search for Roadmap Discussions

```bash
gh search issues --repo {owner}/{repo} "roadmap OR proposal OR enhancement OR design" \
  --limit 50 --json number,title,author,url
```

## KPI 5: Leadership Roles

### Governance Files in Repository

Check for governance documentation:

```bash
gh api repos/{owner}/{repo}/contents/GOVERNANCE.md --jq '.content' | base64 -d
```

Also check:
- `governance/` directory
- `community/` directory
- `STEERING.md`
- `CHARTER.md`

### Repository Tree Search for Governance Docs

```bash
gh api "repos/{owner}/{repo}/git/trees/HEAD?recursive=1" \
  --jq '.tree[] | select(.path | test("governance|steering|charter|tac|advisory|committee"; "i")) | .path'
```

### Organization Members (if public)

```bash
gh api orgs/{owner}/members --paginate --jq '.[].login'
```

Note: Organization membership is often private. This may return limited results.

### Web Search Fallback

When governance files are not found in the repository, use WebSearch to find:
- `"{project_name}" steering committee members`
- `"{project_name}" technical advisory council`
- `"{project_name}" governance leadership`
- `"{project_name}" advisory board`
- Foundation governance pages (CNCF, LF, Apache)

## Rate Limiting Strategy

### Detection

If any `gh` command returns a 403 error or rate limit message:
1. Note the rate limit hit in findings
2. Reduce `--limit` values by 50%
3. Wait 30 seconds before retrying
4. If still limited, report partial data with a note about rate limiting

### Prevention

- Prefer `gh pr list` (bulk) over `gh search prs` (per-author) when possible
- Batch API calls using `--paginate` instead of manual pagination
- Use `--limit` to cap results at reasonable levels (100-500)
- For large employee rosters (>50), focus on employees with resolved GitHub usernames first

## Output Parsing Notes

All `gh` commands with `--json` return JSON arrays. Use `--jq` for server-side filtering when available. For `gh api` commands, use `--jq` for JMESPath-style filtering.

When parsing OWNERS files (YAML), handle both flat lists and nested structures:

```yaml
# Flat list
approvers:
  - username1
  - username2

# Nested with filters
approvers:
  - username1
filters:
  ".*":
    approvers:
      - username2
```
