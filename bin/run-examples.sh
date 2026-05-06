#!/usr/bin/env bash
# Run every example in examples/ and report status + makespan.
#
# Exits 0 if every example reaches OPTIMAL or FEASIBLE; non-zero
# otherwise. Wired up via `make examples`.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

UV="${UV:-uv}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

failed=0

run_example() {
    local dir="$1"
    local name
    name="$(basename "$dir")"
    echo "─── $name ───"

    "$UV" run -- python -m solver.parse_tasks \
        "$dir/tasks.md" "$dir/config.yml" \
        > "$tmp/$name.in.json" 2>/dev/null

    "$UV" run -- python -m solver.scheduler \
        < "$tmp/$name.in.json" \
        > "$tmp/$name.out.json" 2>/dev/null

    "$UV" run -- python -c "
import json, sys
d = json.load(open('$tmp/$name.out.json'))
status = d.get('status', '?')
makespan = d.get('stats', {}).get('makespan', '?')
total_cost = d.get('stats', {}).get('total_cost', None)
extra = f', cost=\$ {total_cost:.4f}' if total_cost else ''
print(f'  status={status} makespan={makespan}{extra}')
sys.exit(0 if status in ('OPTIMAL', 'FEASIBLE') else 1)
"
}

run_example "examples/01-quickstart"    || failed=1
run_example "examples/02-cost-aware"    || failed=1
run_example "examples/03-replan"        || failed=1
run_example "examples/04-multi-provider" || failed=1

# 03-replan also exercises the `solver.replan` CLI.
echo "─── 03-replan: replan after T001,T002 completed ───"
"$UV" run -- python -m solver.replan \
    "$tmp/03-replan.out.json" \
    examples/03-replan/tasks.md \
    examples/03-replan/config.yml \
    --completed T001,T002 \
    > "$tmp/03-replan.replan.json" 2>/dev/null

"$UV" run -- python -c "
import json, sys
d = json.load(open('$tmp/03-replan.replan.json'))
status = d.get('status', '?')
makespan = d.get('stats', {}).get('makespan', '?')
print(f'  status={status} makespan={makespan}')
sys.exit(0 if status in ('OPTIMAL', 'FEASIBLE') else 1)
" || failed=1

if [[ $failed -ne 0 ]]; then
    echo "examples: at least one example failed" >&2
    exit 1
fi

echo "examples: all passed"
