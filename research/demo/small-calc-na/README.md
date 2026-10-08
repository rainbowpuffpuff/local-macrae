# Demo: a small-calc research run (Na⁺), replayable

What a small-calc agent leaves behind under the research protocol (`tasks/research.py`), for the page's Manuscript
view, the mock backend and filming. The science is real: `result.json` and `fig1.png` come from an actual PySCF 2.14.0
run of the flow's calculation on 2026-10-08 (CP-corrected B3LYP/def2-TZVP: −25.46 kcal/mol at 2.20 Å, 141 s). The
agent's side (notebook, draft, revisions) is assembled by `tasks/tests/reference_run.py`.

| file | what |
|---|---|
| `edit_stream.json` | `[{seq, t, op: "write"\|"edit", path, old, new, content}]`, the shape of `GET /api/runs/{id}/manuscript` (CONTRACT v3); `content` is the file after the op. 15 ops: the notebook (5) and the manuscript (1 Write + 9 Edits), including op 7, which deletes a premature def2-SVP claim ("about 32 kcal/mol, close to the full-charge estimate") once the counterpoise-corrected number arrives |
| `draft.md` → `manuscript.md` | the first Write (with **TBD**s) and the final text: title, abstract, introduction, methods with 3 display equations, results, Figure 1, discussion with limitations, references [1]–[5] (group papers, DOIs) |
| `fig1.png` | Figure 1, referenced as `![Figure 1. …](fig1.png)` |
| `NOTES_TO_SELF.md` | the agent's lab notebook: what it tried, what was slow, what to do differently, the `calc-image` capability gap |
| `trajectory.json` | the ATIF trajectory those edits come from (Harbor's `agent/trajectory.json` shape) |
| `result.json` | the real numbers |

Regenerate: `python -m tasks.tests.reference_run --demo research/demo/small-calc-na` (a test checks it's current).
