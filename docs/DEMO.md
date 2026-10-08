# Presenting Macrae live

A script for a **7-minute live demo** (plus a 3-minute cut at the end), with what to say, what to click, and what to
do when something doesn't cooperate. Timings are targets; the bold lines are the ones worth saying word for word.

Site: <https://macrae.acalincarol.workers.dev> · Architecture page: <https://macrae-architecture.pages.dev/> · Traces: `docs/TRACES.md`

> **Read first (final state, 2026-10-09).** This script was written before the last merge. Two things in it were
> not built: the **BFF tasks** (use a finished *Ion–water binding* run instead: its manuscript has equations, a
> figure and citations) and `proof/run_proof.py` (start the benchmark with `POST /api/benchmark/dusk` and `…/dawn`
> and the operator secret, see the README's Deploy section). The page has two panel tabs, **Tasks & runs** and
> **Evolution**; the manuscript is the **Manuscript it wrote** box under a finished run, and lessons, the
> **Capabilities** ledger with the Authority card and **Dusk → Dawn** are sections of the Evolution tab. There is no
> run queue; the limits are rate limits and the daily spend cap.

## The story in one breath

The Jungwirth group's scientists are chemists, biologists and statisticians, not software engineers. Every time
they use a general chatbot they re-explain their own work. Macrae already knows it: it reads the group's papers and
cites them, it runs real calculations in traced cloud sandboxes, and, the Frankenstein part, it notices what it
can't do yet, builds the tool, tests it, installs it and uses it next time, without ever gaining more authority.
**At dusk it couldn't; by dawn it can, and we measured it.**

## Before the demo

### The evening before (≥ 12 h ahead)

- [ ] **Dusk → dawn benchmark done.** Run the proof pack against the live site (`proof/run_proof.py`, see its
      README), or start `POST /api/benchmark/dusk`, then `POST /api/benchmark/dawn` when dusk has finished. Check
      the Dusk → Dawn view shows both columns and a delta. This is the climax; it can't be produced live (each BFF
      run takes 10–15 min).
- [ ] **One finished run of each task** from the dawn state, so you can open them instantly. Write down their run
      ids (`/?run=<id>` opens one). Pick a BFF run whose **Manuscript** tab has math, at least one figure and
      citations.
- [ ] **One run that shows a capability being installed** (gap → create → test → install) and a later run that
      **uses** it. Note both ids.
- [ ] Download one run's **trace zip** and **report** to the laptop (the offline fallback).
- [ ] Voice: one full test conversation on the presenting laptop, in the presenting room if possible.

### 30 minutes before

- [ ] Open the site; the health pill must say **online** (the first request wakes the container; give it a minute).
      `make cf-status` from a terminal shows the same.
- [ ] Ask one typed question, so the paper index and the embedding model are warm.
- [ ] Dark mode on (theme button in the header). Browser zoom 110–125 % for a projector. Close other tabs,
      notifications off, laptop on power.
- [ ] Tabs, left to right: (1) the site, fresh; (2) the finished BFF run (`/?run=<id>`), Manuscript tab;
      (3) the run that installed a capability; (4) `docs/architecture.html` (local file, works offline);
      (5) the downloaded report.
- [ ] Allow the microphone for the site (click the mic once, then end the call). Test the room audio: Jarvis must
      be audible to the audience.
- [ ] Clear the chat (a new tab is enough: the thread lives in that tab's session).

## The script (7:00)

| time | beat | say | do |
|---|---|---|---|
| **0:00–0:40** | **Why** | **"This is Macrae. It's a research assistant for Pavel Jungwirth's group at IOCB Prague: more than two decades of work on ions in water, at interfaces and at membranes, and the charge-scaled force fields behind it."** Everyone has a chatbot; the group's people are chemists, not software engineers, and they keep re-explaining their own work to it. Macrae starts from that work. | Tab 1, the empty chat with the macrae logo. Don't click yet. |
| **0:40–1:40** | **It knows the papers** | "Let me ask it something only the group's papers answer." Read the answer aloud briefly. Point at the `[n]`: **"Every claim cites the paper, first author, year and page."** Point out that it says which parts come from the papers and which don't. | Type: *What charge scaling does the group use for ions like calcium, and why?* Send. Click `[1]`: the source card (title, authors, page, DOI). |
| **1:40–2:40** | **Voice, and a real task** | "You can also just talk to it." | Click the mic. Say: **"Jarvis, how strongly does a potassium ion bind a water molecule? Can you compute it?"** Jarvis answers and offers *Ion–water binding, computed live*; say **"Yes, run it for potassium."** The run opens in the right panel. End the call (or keep it open if the room is quiet). |
| **2:40–3:40** | **It decides, then works, and you see everything** | **"Before it does anything, it plans: which hardware, which stages, what budget, and why."** Then: "Every row is something the agent actually did inside a sandbox on Modal: a paper it read, a command, a calculation. And this meter is real money, growing as it works: tokens on one side, compute seconds on the other." | Point at the 🧭 plan card, then the cost meter, then the timeline as rows arrive (🔎 searches, 📄 reads, 🧮 calcs). Scroll gently; don't hunt for rows. |
| **3:40–4:40** | **It writes the paper** | "While that runs, here is a longer one from earlier: a Bayesian answer to whether 0.8 is the right charge-scaling factor, built on the group's own BFF code." **"This is the manuscript being written, replayed exactly as the agent typed it: drafting, deleting a wrong claim, adding a figure, the math, the citations to the group's papers."** | Tab 2. Manuscript tab, press replay, speed ×4. Let a deletion and a figure happen on screen. |
| **4:40–5:20** | **Proof you can take home** | **"Every run leaves a full trace, and you can download it."** "This zip is every message, every tool call, every file. The report opens offline." | Click **Download trace**, then **Open report**. Scroll the report for three seconds. |
| **5:20–6:20** | **Frankenstein: it builds itself** | "The hackathon brief: an agent that recognises a missing capability, creates it, tests it, installs it and uses it again later." **"Here it noticed it was missing something, wrote the tool and its tests, passed them, installed it, and the next run used it without rebuilding it."** Then: **"Its capabilities evolve. Its authority does not."** The limits on that card are constants in the code: sandbox only, no secrets, no new network hosts, no writing its own registry. | Tab 3, or the **Capabilities** view: the ledger gap → create → test → install → use. Point at the **Authority** card. Then **What it learned**: its notes to itself and this run against the previous one (setup time, total time, cost, errors). |
| **6:20–6:50** | **Dusk → dawn** | **"At dusk it couldn't. By dawn it could."** "Same tasks, same questions. At dusk, no capabilities and no lessons; at dawn, everything it learned. The sun brightens with every capability it installed." Read the two or three biggest deltas aloud (e.g. setup time, total time, cost). | Open **Dusk → Dawn**. Let the sky clock turn once, then the numbers. |
| **6:50–7:00** | **Close** | Glance at tab 1: the potassium run has finished, with its result, cost and citations. **"All of it built on Pavel Jungwirth's group's work, and all of it traceable. Thank you."** | Tab 1, the finished run banner. |

If the potassium run hasn't finished by 6:50, say "it's still computing, here's where it is" and show the cost meter
and the last rows: an honest live run beats a finished one.

## When something doesn't cooperate

| symptom | do | say |
|---|---|---|
| Pill says **Agent offline** at the start | Wait 30–60 s and reload: a sleeping container is starting. Meanwhile show tab 4 (architecture). | "It sleeps when nobody uses it; it's waking up." |
| Still offline after 2 min | `make cf-restart`, then `make cf-status`. Present from tab 4 and the downloaded report (tab 5). | |
| Voice won't connect, or the room is too loud | Type the same sentence. Typed chat uses the same papers and the same tasks. | "Same agent, typed." |
| Jarvis doesn't start the task | Right panel → *Ion–water binding, computed live* → ion **K+** → **Run**. | |
| Task start says **queued, position N** or the daily cap is reached | Open tab 2 or 3 instead and present the finished run. | "Others are using it right now; here's one from earlier." |
| The run fails (⚠️) | Click the error row: the reason is there. Then tab 2. | **"Failures are traced too, and that's exactly what it learns from."** |
| Manuscript tab empty | Use the downloaded report's manuscript section (tab 5). | |
| Dusk → Dawn has no dawn column | Show **What it learned**'s run-vs-run comparison instead. | |
| Projector washes out dark mode | Toggle to light mode (header). | |

## The 3-minute cut

| time | beat |
|---|---|
| 0:00–0:25 | Why (shortened): the group, re-explaining their work, Macrae starts from it. |
| 0:25–1:05 | Typed question → cited answer → source card. Click **Run** on *Ion–water binding* (K+) yourself. |
| 1:05–1:40 | Plan card, cost meter, live rows. "Real sandbox, real money, every step traced." |
| 1:40–2:25 | Capabilities: ledger + Authority card. "Capabilities evolve, authority doesn't." |
| 2:25–2:55 | Dusk → Dawn numbers. "At dusk it couldn't; by dawn it could." |
| 2:55–3:00 | Back to the run. Thank you. |

## Questions you are likely to get

- **"Is the answer really from the papers?"** Click `[n]`: the passage, page and DOI. Jarvis says which parts come
  from general knowledge. Citations are never invented: they come from the search results only.
- **"What did that run cost?"** The meter: LLM $ (tokens × Anthropic's price list) and compute $ (Modal seconds ×
  Modal's rates), with the sources in `server/costs.py`. With a subscription login, LLM $ is the API-equivalent.
- **"Can it break out, or give itself more power?"** No. Authority is fixed in `server/policy.py`: tools run only in
  the sandbox, get no secrets, reach only allowlisted hosts, can't write the registry, and have a time limit. A
  capability that asks for more is rejected and the rejection is in the ledger.
- **"What stops a visitor from spending all the money?"** Per-IP and per-session rate limits, a global cap on
  active Modal runs with a queue, and a daily spend cap; the owner has a kill switch.
- **"Where does it run?"** One Cloudflare Worker (page, API proxy, voice signed URL) in front of a Cloudflare
  Container (the backend) whose state is mirrored to R2; each agent step runs Claude Code in its own Modal sandbox
  through Harbor; voice is ElevenLabs. See <https://macrae-architecture.pages.dev/>.
- **"Can the group use the traces?"** Yes: download any run's zip or report, and `python -m evolve export` turns
  all passing trajectories into a fine-tuning dataset (`docs/TRACES.md`).
- **"Who built it?"** Claude Opus agents working in parallel waves through agent-runner and Harbor, from a written
  contract, each wave merged by an integrator agent; the same tooling that runs the research tasks.
