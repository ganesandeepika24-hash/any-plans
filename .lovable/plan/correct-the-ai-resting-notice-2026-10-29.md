# Correct the AI resting notice

## Change
- Show “AI search is resting. Filters still work.” only after a request cannot reach the agent, or the agent replies with its documented `ai_resting` status (including the daily-limit reply).
- Hide it for successful replies, clarification replies, sign-in problems, and other request errors. Show a separate error notice for those errors instead of claiming the AI is resting.
- Clear an earlier resting notice when a new request begins; use the same rules for searches and follow-ups.

## Checks
- Add frontend regression tests for healthy replies, daily-limit and resting replies, unreachable requests, authentication failures, and other HTTP failures.
- Run the existing search and sign-in tests and verify the notice clears after recovery.
- Check a real signed-in search if a legitimate session becomes available. This project's external Supabase connection currently provides no usable signed-in session to the test browser, so a real signed-in check is blocked; simulated checks will not be reported as a real signed-in pass.

## Findings and scope
- The agent's `/health` currently returns `status: ok`.
- The current page incorrectly treats every request error except HTTP 401 as resting. A healthy health endpoint does not guarantee that an individual search succeeds.
- Keep changes in frontend files; do not change the agent, database, sign-in methods, labels, or daily limits. Publishing is not included in this request.