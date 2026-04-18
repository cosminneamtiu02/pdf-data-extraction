# Feature-graph lifecycle statuses

The node files under `docs/graphs/<SLUG>/` (for this repo, `docs/graphs/PDFX/`)
each carry a `status:` field in their YAML frontmatter. The status is the graph's
answer to the question "where is this node in its build pipeline?" It is read by
humans skimming the tree and may be consumed by scripts that roll up progress, so
the values are a closed vocabulary — not free text. (No such script exists in
this repo today; the closed vocabulary is future-proofing.)

This file is the contract for what each status means and what triggers the
transition into it. If you want to change a node's status, check the criterion
below for the value you are moving to. If none of them fit, stop and ask before
inventing a new status.

## Lifecycle stages for **Feature** nodes

Features move through the stages roughly in this order. They can stay at any
stage indefinitely; the transitions are deliberate, not automatic.

### `detailed`

- Feature stub has been thickened into a full specification via the
  `feature-elicitation` skill (or equivalent interrogation): short description,
  problem, scope, out-of-scope, acceptance criteria, dependencies, technical
  constraints, and open questions are all populated.
- Test scenarios have NOT yet been generated. No implementation exists.
- Entry criterion: the spec reads end-to-end without placeholders, and any
  downstream feature that depends on this one could be elicited against this
  spec without asking "what is this node actually supposed to do?".

### `verifiable`

- Test scenarios have been generated via the `test-generation` skill (unit,
  integration, contract, and — if applicable — e2e scenarios are listed as
  bullet-point descriptions).
- No test code has been written yet. No implementation exists.
- Entry criterion: every acceptance criterion has at least one corresponding
  test scenario in the file, across the right test levels.

### `implementing`

- Implementation PR(s) are in flight. Code is being written against the test
  scenarios.
- Tests may be partially written and partially passing. The feature is not yet
  shipped.
- Entry criterion: at least one draft or open PR exists whose title or body
  references this feature node.

### `implemented`

- All implementation PR(s) for this feature have been merged to `main`.
- The feature's test scenarios are translated into real pytest tests, and those
  tests pass locally under `task check`.
- No known outstanding defects specific to this feature. Bugs may still surface
  later (and get filed as separate issues), but at the moment of the status
  bump there is no open issue against the feature itself.
- Entry criterion: `git log --oneline --all -- <code-path-the-feature-describes>`
  shows merged commits covering the scope, and the feature's test file(s) exist
  and pass.

### `done`

- Reserved for features that additionally require a telemetry / runbook / ADR /
  docs step to be considered closed (not every feature does). When that final
  step lands, the feature moves from `implemented` to `done`.
- Use sparingly. Most features stop at `implemented` because this project
  does not have a formal post-implementation close-out phase for every node.

## Lifecycle stages for **Epic** and **Project** nodes

Epic and project nodes use a coarser vocabulary because their "done" is a
rollup of their children, not a direct action.

### `fully-detailed`

- Every Feature child of this Epic (or every Epic child of this Project) has
  reached at least `detailed`. The tree under this node is completely thickened
  and ready for test-generation and implementation.
- This is the only status most Epic and Project nodes in this repo need to
  carry. Once the rollup is mechanically observable ("all children are at least
  X"), explicit status on the parent is redundant.

## Transition rules (how to bump a status)

1. Check the criterion above for the status you are moving **to**. If it is
   not satisfied, do not bump.
2. Prefer status-only bumps: update the `status:` line in the frontmatter and
   leave the rest of the file untouched, UNLESS the same PR is also correcting
   a factual error elsewhere in the node (e.g. reverting a documentation
   regression or aligning a scope bullet with actual runtime behavior). When a
   factual correction rides along with a status bump, the commit message must
   list both intents so the grep-able history still tells the story.
3. If the bump is bulk (e.g. "every feature whose code is on `main` moves to
   `implemented`"), list every file touched in the commit message so the
   history is grep-able.
4. Do not silently downgrade a status in a local edit. If a feature genuinely
   regresses (rare), open an issue first.

## Non-goals

- These statuses are **not** a substitute for GitHub issue state. A feature at
  `implemented` can still have open follow-up issues against it; those live in
  the issue tracker, not in this file.
- These statuses are **not** enforced by CI today. A future check could compare
  `status: implemented` against the existence of the corresponding code
  directory, but that is out of scope here.
