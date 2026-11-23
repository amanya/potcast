# GitHub Issues

Use GitHub Issues in `amanya/potcast` for tickets and specs. Target that repository explicitly with `gh -R amanya/potcast` or the equivalent repository path in `gh api`.

## Issue operations

- Publish a ticket or spec with `gh issue create --title "<title>" --body-file <file>`.
- Fetch a ticket with `gh issue view <number> --json number,title,body,labels,comments`.
- List tickets with `gh issue list --state <state>`, adding `--label` for the relevant triage role.
- Comment with `gh issue comment <number> --body-file <file>`.
- Apply or remove labels with `gh issue edit <number> --add-label <label>` or `--remove-label <label>`.
- To close an issue with an explanation, post the comment first, then run `gh issue close <number>`.

## Parent issues and dependencies

- Link a child to its parent with `gh issue create --parent <parent>` or `gh issue edit <parent> --add-sub-issue <child>`. If sub-issues are unavailable, list the child in the parent's task list and add `Part of #<parent>` to the child.
- Record a blocker with `gh issue create --blocked-by <blocker>` or `gh issue edit <child> --add-blocked-by <blocker>`. If native dependencies are unavailable, add `Blocked by: #<blocker>` to the child.
- A ticket is unblocked when every blocker is closed. For native dependencies, check `gh api repos/amanya/potcast/issues/<number> --jq .issue_dependencies_summary.blocked_by`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

If enabled, inspect the PR with `gh pr view`, `gh pr view --comments`, and `gh pr diff`. Use `gh pr comment`, `gh pr edit`, and `gh pr close` for triage actions. Identify external PRs by excluding the `OWNER`, `MEMBER`, and `COLLABORATOR` author associations. GitHub shares issue and PR numbers, so check which type a bare `#number` identifies.

## Wayfinding

- Keep Notes, Decisions-so-far, and Fog in a map issue labeled `wayfinder:map`.
- Link each child ticket to the map and label it `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`.
- Claim the first open, unassigned child without open blockers in map order with `gh issue edit <number> --add-assignee @me`.
- Resolve a child by posting the answer, closing the issue, and adding a short decision with a link to the map's Decisions-so-far.
