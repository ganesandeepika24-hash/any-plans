<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

- Keep sign-in calls in a browser-safe helper and agent requests in the API module; explicit follow-ups use the latest successful query ID so new searches and refinements cannot be confused.
- The device position lives only in memory in src/lib/geo.ts and the near-me phrase list lives in src/lib/api.ts; this keeps the position out of the URL and storage and keeps the list matched to docs/api.md section 8.
