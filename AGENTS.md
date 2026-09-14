# Contributor and agent rules

Read [the product contract](docs/PRODUCT.md), [architecture](docs/ARCHITECTURE.md)
and [operations plan](docs/OPERATIONS.md) before non-trivial changes. Preserve the
single-owner, dedicated-bot boundary and deterministic, Hermes-independent runtime.
Proposals are not implemented facts; surface product changes before coding them.

## Delivery

- Use an isolated branch/worktree, not direct edits on the default branch.
- Keep PRs bounded. Put scope, acceptance criteria, exact validation and unresolved
  decisions in the PR rather than duplicating them across chat handoffs.
- Implementer owns valid review corrections and verification; reviewer assesses
  independently. Repository owner retains merge/release authority. Review approval
  is not permission to merge or deploy.
- Read current files and applicable instructions before editing. Preserve existing
  identifiers and history; make schema migrations explicit.
- Check Git author name/email before committing. No special commit trailers are
  required at this stage.

## Evidence and privacy

- Pair domain invariants with regressions; run the complete affected package suite.
- Attribute checks to the actual commit/worktree. Report unrun checks and blockers.
- Docs-only changes require whole-diff whitespace, local-link and consistency checks;
  do not invent a passing runtime suite when none exists.
- Test with synthetic data and isolated storage. Never use production logs as fixtures.
- Do not send live prompts, deploy, migrate or delete data without explicit authorization.
- Keep tokens, private IDs, collections, history and deployment details out of public
  files, commits, PRs and logs. Review the full staged diff before publication.
- Do not add maintained tooling or dependencies solely to validate a small docs change.
