---
step: 6
title: Update the site
summary: Only after the write is effective; never edit generated pages.
---

## With tools

- Do: only after the write state is `effective`. The service regenerates the site and views from the new version; agents do not edit pages. Give the result page link, if there is one, before the final “怎么查、怎么问” section; that section stays last.
- If generation fails: report it in one line. Do not change the answer already given.

## Without tools

- Do: only after the user said the block is committed. The repository's Pages or Actions build the site from the files; do not edit `README.md` or `views/`.
- If the build fails: report it in one line. Do not change the answer already given.

写回仓: this step adds nothing.
