# Narration (owner's script). Keep the owner's voice; tighten only what doesn't fit 90 s.

What we built is a research assistant.
Oh, but everyone has that.
Well, ours is better. Why we say that?

Because it has knowledge on what our lab worked on.
Many of our colleagues are quantum chemists, biologists, statisticians.
They are not software engineers.
Yes, they talk with Claude, Codex.
The problem though, is that they have to repeatedly input data about their own work and that of the lab just to give context to the model.
Models get weaker as you fill the context, so we use a RAG system to make sure only what's relevant is there.

With this, they will be saving tens of hours of time, they're going to accelerate their research, which means they will save your own money, as they're being paid from public taxes.

So what did we build? Why? How is it relevant?
The agent knows our research. It knows how we work.
We give it research questions, it figures out what it needs to run, it knows the literature it runs it on our GPUs, and it produces results.

We box the agents into a trace collection software. This allows recycling the work, using the traces for training

## Owner's notes on the story (added later; these shape the narration)
- Pavel Jungwirth. Storytelling: do it on his research. Build on top of what he did. Celebrate his work. Acknowledge.
- The hackathon track is "Frankenstein": an agent that builds itself. It must recognise a missing capability, create
  it, test it, install it and use it again later. Voice via ElevenLabs. Its capabilities may evolve, but its
  authority may not. "By dawn, show a creature that learned to do things it could not do at dusk."
  CREATE / TEST / INSTALL / EVOLVE.
- Costs: show what a run cost in real money (tokens → $, Modal compute → $) and in time.
- The product is now a dark-mode chat with Jarvis (see web/ and the live site); the film should match it.

## Owner's direction for the film (latest; overrides earlier structure where they conflict)
- Show an END-TO-END research run, fast-forwarded, with the traces that led up to the result visible.
- In parallel, show the paper being written for real: as if someone were typing it, deleting some parts, adding
  figures, rendering formulas, putting in citations. Make it look like a real research agent at work.
- Pavel Jungwirth: the emotional storytelling (celebrate his work, build on it, acknowledge) is part of the voiceover.
- Show how the agent "learns": the notes it writes for itself to evolve, and how the new run's data compares with the
  old run's (time, cost, errors).
- Frankenstein: it recognises a missing capability, creates it, tests it, installs it and uses it again later, with
  ElevenLabs as its voice; capabilities evolve, authority does not; "by dawn, a creature that learned to do things it
  could not do at dusk". Examples it should learn: learning from its traces; pre-installing packages as a container
  image to speed up its runs; pinning package versions; better ways of writing.
- Real evidence from our first Modal runs (use it, it's true): run 1 (Na+–water, PySCF) took 9 min: 170 s to set up the
  agent image, about a minute reinstalling PySCF, a 4-minute sleep while waiting; run 2 (K+) took 5.9 min and hit
  "venv created without pip (ensurepip missing)". These are exactly the lessons/capabilities the agent should learn.
- DUSK → DAWN proof (owner, latest): prove that at dusk it couldn't and at dawn it could. Use the macrae galaxy (the
  logo) as a sky clock: one rotation = dusk to dawn; each run is a particle on an orbit (speed/radius from its real
  time and cost); the central sun is dim and reddish at dusk and grows to full gold as capabilities are installed.
  Then the measured comparison from the real benchmark (same tasks run in the dusk state = no capabilities/lessons,
  and the dawn state = everything learned): success, time, $ cost, errors, setup time. This is the film's climax.
- Owner (latest): don't follow the script word for word. Keep its ideas and most of its lines, but review and improve
  the wording where it makes sense (clearer, more natural spoken English, stronger emotional arc around Pavel
  Jungwirth's research, tighter for 90 s). A voiced draft of the owner's lines (67.7 s) exists; the final narration
  will be regenerated from video/NARRATION.md.
- Owner (latest): CONTINUOUS MOTION. In the first preview the picture froze after ~27 s while the narration went on;
  that is a flaw. In the film nothing may hold still for more than ~1 s: every beat of the narration has its own
  animation (camera moves, UI interactions, typing, charts building, particles, transitions). Use three.js (as in
  ref/tincan/src/space.tsx) for the galaxy / dusk→dawn scenes and the logo dive, React for the UI replicas. The visual
  timeline must cover the entire narration plus a short music tail.
