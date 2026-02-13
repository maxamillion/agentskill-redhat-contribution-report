# Data Sources Reference - gh CLI Commands by KPI

All GitHub data collection uses the `gh` CLI tool exclusively. Never use raw API calls with curl or direct HTTP requests to avoid authentication token management.

## Evaluation Window

All time-series queries use `{cutoff_date}` (a `YYYY-MM-DD` date 6 months before the evaluation date) to bound results to a consistent 6-month evaluation window. The `--limit` parameter is set high enough to capture all results within the date-filtered window. Date filtering is the sole mechanism for scoping results.

Time-series KPIs (1, 2, 4) use date-filtered queries. Current-state KPIs (3, 5) query governance files and leadership positions without date filtering, as these represent point-in-time snapshots. Username resolution (Task 1) intentionally searches all-time history to maximize coverage.

## Workflow Detection & Search API Limitations

### GitHub Search API 1000-Result Cap

The `gh pr list --search` flag uses GitHub's Search API, which has a **hard cap of 1000 results** regardless of the `--limit` parameter. Setting `--limit 10000` will still return at most 1000 results when `--search` is used.

In contrast, `gh pr list` **without `--search`** uses GitHub's GraphQL `pullRequests` connection, which supports full pagination with no cap.

**Rule:** Never use `--search` for repositories that may have more than 1000 PRs in the evaluation window. Instead, fetch all PRs via `gh pr list` (GraphQL) and filter by date locally in python.

### Non-Standard Merge Workflows

Some projects use a "land-and-close" workflow where a bot (e.g., pytorch's `pytorchmergebot`) lands commits directly onto the default branch and then **closes** the PR rather than merging it through GitHub's merge button. In these repos, `--state merged` misses the vast majority of contributions.

**Detection:** Compare merged vs closed PR counts via the Search API:

```bash
python3 -c "
import subprocess, json

def search_count(q):
    r = subprocess.run(['gh','api','search/issues','-X','GET',
        '-f',f'q={q}','-f','per_page=1','--jq','.total_count'],
        capture_output=True, text=True, timeout=30)
    return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0

merged = search_count('repo:{owner}/{repo} is:pr is:merged merged:>{cutoff_date}')
closed = search_count('repo:{owner}/{repo} is:pr is:closed closed:>{cutoff_date}')
landed = closed - merged

if landed > 3 * max(merged, 1) and merged >= 50 and landed >= 100:
    print('WORKFLOW=non-standard — use --state closed')
elif merged > 1000:
    print('WORKFLOW=high-volume — remove --search, filter by date locally')
else:
    print('WORKFLOW=standard')
"
```

**Decision rules:**
- `non-standard`: Fetch `--state closed` PRs (plus `--state merged` for the small fraction merged via GitHub). Verification of closed PRs happens inline during roster matching — each closed-only RH-candidate PR is checked for a closing commit reference in the same python process that does counting, ensuring verification cannot be skipped.
- `high-volume`: Fetch `--state merged` PRs without `--search` to avoid the 1000-cap.
- `standard`: Fetch `--state merged` PRs without `--search` for consistency.

**False-positive warning:** Low-activity repos with many abandoned PRs can trigger false non-standard detection. The minimum thresholds (merged >= 50, landed >= 100) prevent this. Repos below these thresholds use the standard `--state merged` path even if the closed-to-merged ratio is high.

### Commit-Based Landing Verification

For non-standard workflow repos, verify a closed PR was actually landed (not abandoned) by checking its closing events for a commit reference:

```bash
gh api repos/{owner}/{repo}/issues/{pr_number}/events \
  --jq '[.[] | select(.event=="closed") | .commit_id // empty] | length'
```

A non-zero result means the PR was closed by a commit landing on the default branch (i.e., it was landed, not abandoned).

## KPI 1: PR/Commit Contributions

### Bulk PR List (Preferred - avoids per-author rate limit pressure)

Fetch all merged PRs for a repository and filter by date locally in python. **Never use `--search`** for repos with >1000 results — the Search API caps at 1000:

```bash
gh pr list --repo {owner}/{repo} --state merged --limit 5000 \
  --json number,title,author,mergedAt,url
```

For non-standard workflow repos, also fetch closed PRs:

```bash
gh pr list --repo {owner}/{repo} --state closed --limit 20000 \
  --json number,title,author,closedAt,url
```

Filter by date in python after fetching (e.g., `mergedAt >= cutoff_date` or `closedAt >= cutoff_date`).

Cross-reference the `author.login` field against the employee roster GitHub usernames.

### Per-Author PR Search

```bash
gh search prs --author {github_username} --repo {owner}/{repo} --merged \
  --merged ">={cutoff_date}" --limit 500 \
  --json number,title,repository,updatedAt,url
```

### Commit Search by Author

```bash
gh search commits --author {github_username} --repo {owner}/{repo} \
  --author-date ">={cutoff_date}" --limit 100 \
  --json sha,commit,repository,url
```

### Co-authored Commits

Search for co-authored-by trailers in commit messages:

```bash
gh api "repos/{owner}/{repo}/commits?since={cutoff_date}T00:00:00Z" --paginate --jq '.[].commit.message' | \
  grep -i "co-authored-by.*{employee_name_or_email}"
```

### Git Log Email Matching (Tier 2 — for unresolved employees)

**Primary method:** Search the full commit history via GitHub API without cloning:

```bash
gh search commits --author-email "{employee_email}" --repo {owner}/{repo} \
  --author-date ">={cutoff_date}" --limit 200 \
  --json sha,commit,author,repository
```

Note: `gh search commits --author-email` requires an exact email address, not a suffix pattern. Run this once per unresolved employee using their `@redhat.com` email from the roster.

If the employee has commits, extract their GitHub username from the `author.login` field of the results.

**Fallback method:** If `gh search commits` returns no results or hits rate limits, clone with deeper history:

```bash
git clone --depth 500 --filter=blob:none https://github.com/{owner}/{repo}.git /tmp/{repo}
git -C /tmp/{repo} log --all --since="{cutoff_date}" --format='%ae|%an|%H|%s' | grep -i '@redhat.com'
```

The depth is set to 500 (up from 100) to improve coverage for high-velocity repositories.

## KPI 2: Release Management

### List All Releases

```bash
gh api repos/{owner}/{repo}/releases --paginate \
  --jq '[.[] | select(.published_at >= "{cutoff_date}")] | .[] | {tag: .tag_name, author: .author.login, name: .name, date: .published_at, url: .html_url}'
```

### Release Tags with Authors

```bash
gh release list --repo {owner}/{repo} --limit 100 \
  --json tagName,author,publishedAt,isLatest
```

Post-filter by date using python since `gh release list` has no server-side date filter:
```bash
python3 -c "
import json, sys
data = json.load(sys.stdin)
filtered = [r for r in data if r.get('publishedAt','') >= '{cutoff_date}']
json.dump(filtered, sys.stdout, indent=2)
"
```

Cross-reference `author.login` with employee roster to identify Red Hat release managers.

### Bot Filtering

Many releases are created by CI bots, not human release managers. Filter out bot accounts before attribution:

```bash
gh api "repos/{owner}/{repo}/releases" --paginate \
  --jq '[.[] | select(.published_at >= "{cutoff_date}") | select(.author.login | test("\\[bot\\]$") | not) | select(.author.login | IN("github-actions", "dependabot", "renovate", "mergify", "semantic-release-bot", "release-please", "goreleaser", "pypi-bot") | not) | {tag: .tag_name, author: .author.login, date: .published_at}]'
```

If all releases are authored by bots, the project likely uses automated release pipelines. In that case:
1. Do not attribute release management to the bot
2. Proceed to "Release Notes Human Attribution Search" and "Pre-Release PR Merger" below to identify human release managers

### Release Notes Human Attribution Search

Search release bodies for explicit human attribution patterns:

```bash
gh api "repos/{owner}/{repo}/releases" --paginate \
  --jq '.[] | select(.body != null) | {tag: .tag_name, body: .body}' | \
  grep -i -E "release managed by|release captain|release lead|release manager|cut by|prepared by|coordinated by"
```

Cross-reference any names or usernames found against the employee roster.

### Pre-Release PR Merger

Identify who merged the last PRs before each release tag as a secondary signal for release involvement:

```bash
gh api "repos/{owner}/{repo}/releases?per_page=10" --jq '.[].tag_name' | while read tag; do
  echo "=== Release: $tag ==="
  gh pr list --repo {owner}/{repo} --state merged --base main --limit 5 \
    --json number,title,author,mergedBy,mergedAt \
    --jq "sort_by(.mergedAt) | reverse | .[:5]"
done
```

Cross-reference `mergedBy.login` against the employee roster. Frequent pre-release mergers may indicate release management responsibility even when releases are cut by bots.

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
gh issue list --repo {owner}/{repo} --label "enhancement" --state all --limit 5000 \
  --search "created:>{cutoff_date}" \
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
gh search issues --repo {owner}/{repo} "roadmap OR proposal OR enhancement OR design created:>{cutoff_date}" \
  --limit 1000 --json number,title,author,url
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
- Use `--limit` to cap results at levels that capture the full date-filtered window
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
