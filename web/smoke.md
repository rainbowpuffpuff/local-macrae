# web smoke checklist

Run before a demo, against the real deployment (or locally: `node cloudflare/dev/mock-backend.mjs` and
`node cloudflare/dev/serve.mjs`, then open http://127.0.0.1:8787). Automated checks: `node --test web/tests cloudflare/test`,
and the headless run in `web/review/review.mjs` (see its header).

## First look
- [ ] Header: the macrae orbit mark + wordmark, "Jungwirth group · IOCB Prague", the pill, theme and panel buttons.
      The tab shows the orbit favicon. No console errors.
- [ ] The pill says `Online · N papers · Modal ready` (green dot). Clicking it re-checks.
- [ ] The thread shows the hero (large mark, one line about Jarvis) and 3 suggestion chips; the composer sits at the
      bottom centre with the microphone ("Talk") and Send.
- [ ] Right panel "Tasks & runs": a card per task (icon, title, subtitle, prompt, inputs prefilled with their
      defaults; small-calc's Ion is a dropdown), then "Recent runs", newest first.
- [ ] Theme button switches light (white, navy, periwinkle) / dark (navy night, cream, gold). A reload keeps it.

## Typed questions (no voice needed)
- [ ] A chip or a typed question shows a user bubble, "Searching the papers…", then a Jarvis answer: one bullet per
      passage ending in a small `[n]` button, and numbered source chips underneath.
- [ ] Clicking `[1]` or chip 1 opens source card 1 (title, authors, journal · year · page, quote, DOI). The DOI link
      opens the paper in a new tab. "Show all" / "Hide all" works.
- [ ] Enter sends, Shift+Enter makes a new line.

## Voice
- [ ] The microphone button asks for the microphone, then turns dark with a gold stop square ("End"); a mute button
      appears and the status says "Listening…". The hint says typed messages go to Jarvis.
- [ ] Your words appear as bubbles marked "spoken"; Jarvis's answers as Jarvis messages marked "voice". `[n]` in an
      answer is a button.
- [ ] When the agent calls `show_citations`, source chips appear under its answer; `[n]` opens card n.
- [ ] Tool calls show as small grey lines ("🔎 Searched the papers").
- [ ] Mute turns the mute button red. Typing during a call sends the text to the agent.
- [ ] Asking Jarvis to run a task posts a Jarvis message with a run chip and opens the run in the panel.
- [ ] "End" hangs up. A blocked mic, or voice not configured, gives a clear message, not a silent failure.

## Tasks and the current run
- [ ] "Run" on a card posts "Starting **Title** … on Modal" with a run chip, folds the task list, and shows
      "Current run" with the title, a blue "Running" state and a ticking timer. The URL becomes `/?run=<id>`.
- [ ] Trace events stream in every ~1.5 s with icons: 📄 read, 🔎 search, 🧮 calc, ✍️ write, ✅ result, ⚠️ error.
      Rows are visible as soon as they arrive (no blank timeline next to non-zero counters).
- [ ] Counters (read, searches, calcs, files) go up as events arrive. Steps show `card[0] agent · done · reward 1`.
- [ ] "Papers it cited" lists one card per paper. Long details are clipped with "Show more".
- [ ] At the end: a green "Finished in …" banner (or red "Failed" with the last error); the state pill and the run chip
      in the thread say Done; Jarvis posts the outcome with the cited papers as sources.
- [ ] Reload `/?run=<id>`: the same run, and the conversation is still there. Back/forward closes/reopens the run.
- [ ] The header never sticks on "Opening the run…"/"Starting" or the pill on "Checking…" once events show.

## v2: plan, costs, evolution (details in web/V2_NOTES.md)
- [ ] Starting a task: within a few seconds the run's **first card** is the planner's decision ("Decided: GPU A10G,
      N stages, budget $X", with "because …", the stages and the params). The chat's run chip shows the same line.
      If the planner failed: an amber "Planner unavailable: task defaults" card.
- [ ] The **cost meter** under it counts up while the run goes: total, LLM, compute, tokens, the budget bar, and the
      time per phase (plan/setup/work/check) with the current phase pulsing. "Per step" opens a table.
- [ ] Agent tool calls (reads, commands, calculations) show **while the agent step is still running**, each with its
      duration and cost in the small print. The line under the trace says what happened last and how long ago.
- [ ] After ~12 s the trace follows its newest row; the cost strip sticks to the top of the panel. Scrolling the
      panel yourself pauses the follow for 10 s.
- [ ] At the end: "Finished in … for $X"; Jarvis states the cost (LLM + compute); the chip shows it. Shortly after,
      Jarvis says "From that run I learned: …" with a "Run over run" link that opens the Evolution tab.
- [ ] **Evolution** tab (or `/?view=evolution`): a card per task with time and cost sparklines, ✓/✗ per run,
      the lessons with evidence links. An evidence link opens that run and flashes the event it points at.
- [ ] On a phone the peek bar shows the run's live cost; Evolution fits without sideways scrolling.

## Backend down
- [ ] Stop the backend (or set a wrong `BACKEND_URL`): a red banner "Jarvis is offline…" with "Check again", the pill
      turns red "Agent offline", task cards are greyed out and can't start. No blank page.
- [ ] A typed question gets a clear Jarvis reply that the backend is offline.
- [ ] An open run says "Jarvis is offline … this keeps trying" and recovers when the backend is back.
- [ ] When the backend comes back, the banner goes away and tasks are enabled again.

## Phone (≈390 px wide)
- [ ] One column: header, thread, composer; no sideways scrolling. The pill just says "Online".
- [ ] A bar "Tasks & runs" peeks above the bottom; tapping it opens the sheet (tasks, current run, recent runs) over a
      scrim; tapping the scrim or the bar closes it. Starting a run opens the sheet on the run.
