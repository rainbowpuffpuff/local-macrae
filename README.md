# iocb-chat

Literature chat (RAG, maybe fine-tune later) for the Pavel Jungwirth group at IOCB Prague.
Goal: answer questions over the group's papers, the methods in them, and the papers they cite.

## Status

Step 1 done: list of group publications.

- `scripts/fetch_group_pubs.py` pulls the list from the IOCB site API
  (`https://jungwirth.group.uochb.cz/en/api/publications`, paged 10 per call).
- `data/group_publications.{json,csv,md}`: 479 papers, 2004-2026, 473 with a DOI.

Notes on the data:

- The site lists papers by any group member, so 206 of the 479 don't have P. Jungwirth as author.
- Nothing before 2004 (site only covers the IOCB years).
- 6 entries have no DOI (Czech popular-science articles, RSC book chapters, one proceedings).
  One missing DOI was filled from Crossref (`DOI_FIXES` in the script).
- One Early View paper has no year yet (site shows it as 1970).

Run: `python3 scripts/fetch_group_pubs.py`

## Next

- References of each paper via OpenAlex (by DOI).
- Open-access full text via Unpaywall; ACS papers are mostly paywalled, need IOCB access.
- Chunk methods sections, embed, build the retrieval + chat part.
