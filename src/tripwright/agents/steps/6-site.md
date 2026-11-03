---
step: 6
title: Update the site
summary: Only after the write is effective; never edit generated pages.
---

## With tools

- Do: only after the write state is `effective`. The service regenerates the site and views from the new version; agents do not edit pages. Give the result page link, if there is one, before the final “怎么查、怎么问” section; that section stays last.
- If generation fails: report it in one line. Do not change the answer already given.
- Share page for friends (only when the trip repository has `.tripwright/share/config.json`): after the write is `effective`, run `tripwright share publish` (without the MCP tools: `uvx --from ./tools/<wheel> tripwright --repo . share publish`). Exit 0: the page is updated; nothing to tell unless the user asked. Exit 2: the privacy scan blocked this version and nothing was pushed (the page online stays as it was); the next `turn` carries a `share_blocked` notice with the categories and pages. Reword those parts of the records through `propose` and `commit`, by category; never write the value it found into the answer or the records. Then publish again.
- The first public page (`tripwright share publish --create`) publishes to people other than the user: stop and ask first. Say that GitHub Free serves Pages only from a public repository, that the repository holds only the pages (no records, no names), and its name (`trip-<trip id>`, or the one the user gives with `--name`). Create it only after the user agrees.

## Without tools

- Do: only after the user said the block is committed. The repository's Pages or Actions build the site from the files; do not edit `README.md` or `views/`.
- If the build fails: report it in one line. Do not change the answer already given.

写回仓: this step adds nothing.
