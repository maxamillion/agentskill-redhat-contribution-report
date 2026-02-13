#!/usr/bin/env python3
"""Detect merge workflow type for a GitHub repository."""
import argparse, subprocess

def search_count(q):
    r = subprocess.run(['gh','api','search/issues','-X','GET',
        '-f',f'q={q}','-f','per_page=1','--jq','.total_count'],
        capture_output=True, text=True, timeout=30)
    return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--owner', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--cutoff', required=True)
    args = p.parse_args()

    merged = search_count(f'repo:{args.owner}/{args.repo} is:pr is:merged merged:>{args.cutoff}')
    closed = search_count(f'repo:{args.owner}/{args.repo} is:pr is:closed closed:>{args.cutoff}')
    landed = closed - merged

    if landed > 3 * max(merged, 1) and merged >= 50 and landed >= 100:
        print(f'WORKFLOW=non-standard  MERGED={merged}  CLOSED={closed}  LANDED={landed}')
    elif merged > 1000:
        print(f'WORKFLOW=high-volume  MERGED={merged}  CLOSED={closed}')
    else:
        print(f'WORKFLOW=standard  MERGED={merged}  CLOSED={closed}')

if __name__ == '__main__':
    main()
