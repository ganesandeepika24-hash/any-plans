# Complete sign-in and follow-up search, then publish

## Confirmed now
- AI search already sends the signed-in user's Supabase token as a Bearer header to `https://any-plans.onrender.com/search`.
- Sign-in currently uses email and password, not magic links or Google.
- The `/refine` helper exists, but the screen does not call it.

## Changes
1. Replace password sign-in with email magic links and Google sign-in. Keep sign-in only, with no profile table.
2. Add a follow-up field after a successful AI search. Send “make it cheaper” and other follow-ups to `/refine`, using the latest successful query ID; keep clarification questions on `/search`.
3. Add focused frontend tests for sign-in calls, Bearer headers, and refinement selection. Leave near-me untouched and do not edit the agent, jobs, database, evals, or docs.
4. Label the search button “AI search” with a small sparkle icon. Keep the filter panel labelled “Filters”, without an icon. When AI is resting or unreachable, show “AI search is resting. Filters still work.” beside the AI search button.

## Publish and checks
- The quick security scan found no critical findings. Publish after the frontend tests pass.
- Check the actual published site for sign-in choices, Google redirect, anonymous filter results and URL persistence.
- Test email delivery only with an email address supplied for testing. A real magic-link completion requires inbox access.
- Exercise signed-in `/search` and “make it cheaper” `/refine` on the published site only if a legitimate session is available. External Supabase sessions cannot be minted through Lovable; do not substitute mocked responses for an end-to-end pass.
- Report passed, failed, and unverified separately, including any Supabase provider/redirect configuration problems.

## Technical details
- Use Supabase `signInWithOtp` and `signInWithOAuth({ provider: 'google' })`, redirecting to the current site's public home page.
- Keep Supabase URL/key in existing environment variables. No new server code or database changes.
- Preserve the API's latest successful `query_id` across refinement responses; never refine a clarification-only query.