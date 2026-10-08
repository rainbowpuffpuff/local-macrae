# research/

The research side of macrae: where the agent's research tasks come from, and what a research run produces.

| path | what |
|---|---|
| `scout/` | the scouting reports behind the BFF research ideas: the group's papers (`papers.md`, `papers_openalex.json`), the BFF code (`bff_code.md`) and site (`bff_site.md`) |
| `demo/small-calc-na/` | a complete research run to replay: the manuscript's edit stream (draft → revisions → final, with LaTeX math, a real figure and [n] citations), the lab notebook, the trajectory and the real numbers |

How the agent works as a researcher (every research task, `tasks/research.py`): it keeps a lab notebook
(`NOTES_TO_SELF.md`: what it tried, what was slow, what to do differently next time, missing capabilities) that
evolve learns from after the run, and writes `results/manuscript.md` progressively through the Write/Edit tools:
drafted early with **TBD** values, revised as each result arrives, wrong claims deleted, checked at the end for
math, figures, citations and agreement with `result.json` (`tasks/checks.py research`). See `tasks/V3_NOTES.md`.
