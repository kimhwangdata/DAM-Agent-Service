# Commit and push

Create a simple Conventional Commit and push it.

Arguments: $ARGUMENTS (optional — free-form description of the change)

## Steps

1. Run `git status` and `git diff` to see what changed.
   Stage the relevant files (`git add`), or ask the user if it's unclear
   what should be included.

2. Check the diff for secrets (app keys, secret keys, account numbers,
   `.env` files). Stop and warn the user if any appear.

3. Write a commit message — subject ≤50 chars, imperative mood, with a
   simple type prefix:
   - `feat:` new functionality
   - `fix:` bug fix
   - `docs:` documentation only
   - `chore:` everything else (deps, tooling, config)

   Examples:
   - `feat: add watchlist price table`
   - `fix: handle expired refresh token on video lookup`
   - `docs: update Phase1 plan checklist`

4. Show the user the proposed commit message and ask for approval.

5. On approval, run `git commit` and then `git push`.
