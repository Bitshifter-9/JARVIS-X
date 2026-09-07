# 50 features — the second brain

The goal in one line: **JARVIS X should quietly watch, learn you, and become a model of
you** — how you speak, what you consume, what you keep forgetting, what you should
improve — running automatically on the phone and the Mac, and doing it on a tight budget
and small storage. The far horizon is a portable "self-model" you could one day hand to
an embodied agent (a robot of yourself). Every feature below is written with that in mind.

Two rules the whole list obeys:

1. **On-device first, cloud last.** Capture, transcribe, embed, and summarise locally
   (Ollama on the Mac, a small quantised model on the phone). The paid cloud brain is for
   the moments you actually ask — never for the background firehose. This is the only way
   the budget survives continuous capture.
2. **Store the cheap representation, throw away the expensive one.** Keep the *text* of
   what a screen said, not the pixels; the *transcript* of what was heard, not the audio;
   the *summary* of a day, not every event. Then quantise the embeddings and tier the rest
   to cold storage or deletion. (Techniques and numbers in **§ Optimization**.)

Legend: ✅ done · 🚧 in progress · ⬜ queued. Grouped A–E; build order in **§ Phasing**.

Built on what already exists: the event/source pipeline, the knowledge graph
(`services/graph`, with GraphRAG), memories + profile, the insights service (labels,
spending, streaks, anomalies), personas, the signed device nodes (Mac + phone), and the
LLM router cascade with a local Ollama fallback.

## Shipped so far

Phase 1 (the affordable foundation) is done, and Phase 2 has begun — all on-budget:
regex, a **local** embedder (server CPU, no API), and plain Postgres, no new cloud cost.

- ✅ **#26 Life search** — one keyword box over mail, deadlines, chat and memory.
- ✅ **#9 Voice quick-capture** — dictate a thought; dated → deadline, else a searchable memory.
- ✅ **#22 Commitment tracking** — catch first-person promises ("I'll send it Friday") and track them.
- ✅ **#24 About-to-forget** — a "Coming up" surface plus a proactive "you said you'd…" nudge.
- ✅ **Memory optimisation** — mem0-style consolidation (dedupe near-identical) + a *forget* tier
  (recall reinforces; unused episodic memories age out past a TTL). This is the storage-budget spine.
- 🚧 **#14 Preference learning — dislike a reminder.** Thumbs-down a deadline and JARVIS mutes its
  sender/channel: future mail/messages from that source stop becoming reminders, and the ones
  already in the list are cleared. A "Muted" section on Goals lists them, un-mute to reverse.
  Mirrored phone notifications are muted **per chat/group**, not per app — silencing one WhatsApp
  group leaves the rest. The first slice of the reject→learn loop; a full ranker comes later.
- ✅ **#28 Dropped-thread finder** — an "Owe a reply?" card: people who asked you something a few
  hours ago that you may not have answered (from the triage `needs_reply` reads), muted senders
  excluded so it composes with the dislike learning above.
- ✅ **#21 Spaced-repetition resurfacing** — a "Worth remembering" card brings important memories
  back on a doubling forgetting curve, so what you learned once doesn't evaporate ("what I forget
  that are important"). Curve advances server-side, paced to at most once a day.
- ✅ **#23 Instant contextual recall** — "Ask JARVIS" in the search box answers a question from
  your whole captured world (mail, deadlines, chat, memory) with each claim sourced; free keyword
  retrieval, cascade synthesis (local/free first), degrades to the sources if no model answers.

Still open in the optimisation spine: binary-quantised vectors + full hot→warm→cold tiering —
deferred until the data volume makes them worth the complexity (the roadmap's own cheap-first rule).


---

## A. Ambient capture — the raw material (on-device, opt-in, privacy-gated)

1. ⬜ **Screen memory (Mac).** Periodic on-device OCR of the active window → store the
   *text + app + title + time*, never the screenshot. Rewind-style recall without the
   disk cost. (`macnode` + `SourceObject` kind `screen`.)
2. ⬜ **Ambient audio → transcript.** Opt-in, on-device Whisper (already used by
   `macnode voice`); store the *diarised text*, discard the audio within seconds.
   Consent-gated (never record others without a visible indicator).
3. ⬜ **App & usage timeline, unified.** Extend the Android `UsageStats` sampler to the
   Mac (foreground app + duration) → one cross-device "where your time went" stream.
4. ⬜ **Reading & watching log.** Capture what you open (browser history, YouTube,
   articles, PDFs) and auto-summarise the content, not just the URL.
5. ⬜ **Location trails + place learning.** Significant-location detection (home / work /
   gym / a friend's) from the phone's coarse location, as context, not a map.
6. ⬜ **Media diary.** What you watched/listened to, with a one-line "why it mattered / what
   you took from it," so consumption becomes recall-able knowledge.
7. ⬜ **Clipboard history.** A searchable timeline of everything you copied (building on the
   synced clipboard already shipped).
8. ⬜ **Photo & screenshot semantic index.** On-device caption + OCR every image once, store
   the caption, make your camera roll searchable ("that receipt from Goa").
9. ✅ **One-tap / voice quick-capture.** Hold-to-talk anywhere → transcribed, classified,
   filed — the frictionless inbox for a fleeting thought.
10. ⬜ **Meeting & call capture.** Auto-transcribe (with consent), then extract summary,
    decisions, and action items into tasks.

## B. The learning layer — becoming *you*

11. ⬜ **Personal style model.** Learn your writing/speaking voice (sentence length, tone,
    emoji, sign-offs) so every draft sounds like you, not like a chatbot.
12. ⬜ **Speech-pattern profile.** Pace, filler words, the phrases you lean on — used both to
    draft in your voice and to coach (§D).
13. ⬜ **Personal phrasebook.** Your recurring jargon, names, and acronyms → feeds the
    transcriber and drafter so it stops mishearing "Guru Vai" as "guruvhy."
14. 🚧 **Preference learning from choices.** Every accept/reject/edit of a suggestion trains a
    lightweight ranker — the system's taste converges on yours. *Shipped:* dislike a reminder →
    its sender/channel is muted so it stops nagging (a "Muted" section on Goals holds the rules).
15. ⬜ **Rhythm model.** When you focus, when you slump, your energy curve by hour/day —
    so JARVIS schedules and nudges *when you're actually receptive*.
16. ⬜ **Deepened relationship graph.** Who matters, how you talk to them, your usual
    cadence — extends the existing knowledge graph with people-edges + contact intervals.
17. ⬜ **Interest drift model.** What you care about *now* vs. what you're drifting from, so
    briefings track your actual attention, not a stale profile.
18. ⬜ **Private mood/sentiment trend.** From journals and your own messages, on-device only —
    a gentle line chart, never shared, feeds the coach.
19. ⬜ **Digital twin persona.** A persona that answers *as you* (drafting, rehearsing a hard
    conversation, "what would I say?") — the seed of the future self-model.
20. ⬜ **Personal LoRA (the actual learning).** Periodically fine-tune a small on-device
    adapter on your corpus (style, facts, preferences) so the local model *is* yours —
    cheap, incremental, offline. This is "improve our model" made literal.

## C. Recall & anti-forgetting — the second-brain core

21. ✅ **Spaced-repetition resurfacing.** Important notes/decisions resurface on a forgetting
    curve, so what you learned once doesn't evaporate. Durable memories (semantic/source) of
    high importance rest for an interval that *doubles* each time they're brought back; a
    "Worth remembering" card on Insights shows the current set, paced to at most once a day.
22. ✅ **Commitment tracking.** "I'll send it Friday," "let's do coffee next week" — extracted
    from your messages and tracked to done, so you keep your word.
23. ✅ **Instant contextual recall.** "What did I decide about the API?" → answered from
    everything captured, each claim with a source. Retrieval is the free keyword tier (per-word
    search + merge over mail, deadlines, chat, memory); synthesis runs the LLM cascade
    (free/local first, a paid call only because *you* asked) and degrades to the ranked sources
    if no model answers. An "Ask JARVIS" action in the life-search box. (Semantic/GraphRAG
    re-ranking is the later optimisation.)
24. ✅ **About-to-forget reminders.** Surfaces the thing *just before* you need it — the name
    before the meeting, the gift before the birthday, the promise before you see them.
25. ⬜ **"What mattered" digest.** Auto daily/weekly rollup of the few things that mattered
    (extends the away-digest already shipped).
26. ✅ **One search box over your whole life.** Mail, chats, screens, notes, media — one
    semantic search, ranked, with time and source.
27. ⬜ **Rediscover.** Surfaces an old idea/note relevant to what you're doing *right now* —
    serendipity on purpose.
28. ✅ **Dropped-thread finder.** People who asked you something a few hours ago you may not
    have answered — read off the triage ``needs_reply`` classifications, one row per sender,
    muted senders excluded. An "Owe a reply?" card on Insights.
29. ⬜ **Knowledge-gap detector.** Topics you keep needing but never learned → offered as a
    micro-lesson (feeds §D #35).
30. ⬜ **Time-travel reconstruction.** "What was I working on last Tuesday?" — a rebuilt day
    from the capture streams.

## D. Self-improvement — the coach

31. ⬜ **Habit coach.** Beyond streaks (shipped): forming new habits, smallest-next-step when
    you're slipping, celebrating when you're on a roll.
32. ⬜ **Auto weekly self-review.** Wins, slips, one pattern, one focus for next week —
    generated, not written by you.
33. ⬜ **Behaviour nudges.** "You doom-scroll after 11pm," "you reply to family late" —
    from the capture streams, kind not naggy (extends anomaly nudges, shipped).
34. ⬜ **Skill & goal progress.** Milestones, trajectory, honest "on track / behind."
35. ⬜ **Personalised micro-lessons.** "How to improve X" turned into 3-minute lessons from
    *your* gaps, delivered on a spaced schedule.
36. ⬜ **Focus analytics.** Deep-work time, top distraction sources ranked, best focus window.
37. ⬜ **Communication coach.** Your reply latency, tone drift, who you ghost — with concrete
    fixes.
38. ⬜ **Energy/health correlation.** If you connect sleep/steps, correlate them with your
    productivity so you learn what actually moves your day.
39. ⬜ **Decision journal + outcome review.** Log a decision + your reasoning; weeks later,
    "did it work?" — so you learn to decide better.
40. ⬜ **Accountability mode.** Opt-in check-ins that hold you to the commitments in #22.

## E. Full automation — hands-free on mobile + Mac

41. ⬜ **Autonomous morning brief & evening wind-down.** Run themselves; no prompt.
42. ⬜ **Auto-triage everything.** Mail, messages, notifications → only the few that need
    *you* surface; the rest are summarised or handled.
43. ⬜ **Draft-in-your-voice, one-tap send.** Replies pre-written in your style (§B #11),
    queued for a single tap.
44. ⬜ **Auto-tasks from commitments.** Captured promises become tracked tasks with no typing.
45. ⬜ **Overnight agent.** Within standing permissions, it tidies, follows up, and prepares
    while you sleep; every effectful step still auditable.
46. ⬜ **True cross-device continuity.** Start on the Mac, finish on the phone, seamlessly —
    one brain, one context (the architecture already shares state).
47. ⬜ **Fully hands-free voice mode.** Wake → understand → do → confirm, no screen.
48. ⬜ **Scheduled autonomous workflows.** Weekly review, inbox cleanup, follow-up sweeps —
    on a cron, reported after.
49. ⬜ **Smart notification layer.** Batched, ranked, with a live-activity for the *one* thing
    that matters now (builds on the live alerts already shipped).
50. ⬜ **Self-model export (robot-ready).** A single portable bundle — style model,
    preferences, knowledge graph, routines, phrasebook, values — versioned and encrypted,
    that a future embodied agent could load to *be* you. The reason for all of the above.

---

## Optimization — the budget & storage strategy (cross-cutting, non-negotiable)

Continuous capture is a cost bomb unless every stage is engineered down. The principles:

- **Local inference for the firehose.** Transcription (Whisper), OCR (Vision/Tesseract),
  captioning, embeddings, and summarisation run **on-device** — the cloud brain is only
  hit when *you* ask. Quantised 4-bit small models run at usable speed on a phone
  (≈2 GB footprint, ~18 tok/s on recent hardware) and free on the Mac via Ollama.
- **Discard the raw, keep the derived.** Text of a screen ≪ its screenshot; a transcript ≪
  its audio; a day's summary ≪ its events. Capture → derive → **delete the source**
  (audio within seconds, frames after OCR).
- **Quantise every embedding.** **Binary quantization** cuts vector storage ~**24×** and
  queries ~80% faster at 90%+ recall; **scalar (int8)** cuts ~4× at >99% recall.
  Store binary for the coarse search, keep a few full-fidelity vectors only for rescoring
  the top hits. A naïve float32 1536-d store is 6 KB/vector — 100 M vectors = 600 GB;
  binary makes the same set ≈25 GB.
- **Matryoshka / truncated dimensions.** Store 256–384 dims instead of 1536 where recall
  allows — another 4–6× before quantization even applies.
- **mem0-style memory, not a log.** Don't accumulate everything: **extract → consolidate**
  with ADD / UPDATE / DELETE / NOOP so the memory stays small, current, and non-redundant.
  This also *raises* answer quality (the field's benchmarks show consolidation + a temporal
  graph beating raw recall by double digits).
- **Rolling summarisation + tiering.** Hot (last N days, full text) → warm (weekly
  summaries, raw dropped) → cold (monthly gist, compressed) → **forget** (TTL delete of
  anything never recalled). Storage grows sub-linearly with time.
- **Selective capture.** Skip idle screens, duplicate frames, and low-signal moments;
  hash-dedupe before storing. Most of the firehose is noise — don't pay to keep it.
- **Cheap-tier routing for background work.** The router cascade already prefers free/cheap
  providers; force *all* background jobs (labelling, summarising, embedding) to the local
  or free tiers, reserving paid calls for interactive asks. Batch and run off-peak.
- **Privacy budget = storage budget.** The stuff you'd never want stored is usually the
  stuff you'll never recall — declining to keep it saves money *and* is the right default.

## Privacy & control (this list is sensitive by nature)

- Every capture stream is **off by default**, toggled per-stream, with a visible indicator
  when recording, and a hard "pause everything" switch.
- Raw capture is processed on-device and never leaves it; only your own account holds the
  derived text (the existing "nothing syncs device-to-device, one shared account" model).
- One-tap **wipe** of any stream or the whole second brain (the account-wipe already ships).
- Recording audio around other people is consent-gated and region-aware.

## Phasing (highest leverage first, cheapest first)

- **Phase 1 — the foundation (build before any capture):** the optimization layer —
  on-device embed/summarise, binary-quantised vector store, mem0-style consolidation,
  tiering + TTL. Nothing else is affordable without this. Plus #26 (life search) and #9
  (quick-capture) to make it immediately useful.
- **Phase 2 — cheap high-value capture:** #3 usage timeline, #4 reading/watching, #6 media
  diary, #7 clipboard history, #22 commitments, #24 about-to-forget.
- **Phase 3 — learning you:** #11 style model, #13 phrasebook, #14 preference learning,
  #15 rhythm, #16 relationship graph; then #43 draft-in-your-voice pays it back.
- **Phase 4 — the coach & automation:** #31–#40 and #41–#49.
- **Phase 5 — the heavy/optional capture & the twin:** #1 screen memory, #2 ambient audio,
  #20 personal LoRA, #19 digital twin, #50 self-model export.

## Sources

- [Best second brain apps in 2026 — Tana](https://tana.inc/blog/best-second-brain-apps-2026),
  [Rewind/Limitless review — AIGearBase](https://aigearbase.com/tool/rewind-ai) (ambient
  capture, the "records everything you see/say/hear" model, now Limitless).
- [State of AI Agent Memory 2026 — mem0](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
  [Mem0 paper (arXiv 2504.19413)](https://arxiv.org/pdf/2504.19413),
  [Best AI Agent Memory Frameworks 2026 — Atlan](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)
  (extract/consolidate memory, temporal knowledge graphs beating raw recall).
- [Edge LLM Deployment 2025 Guide](https://kodekx-solutions.medium.com/edge-llm-deployment-on-small-devices-the-2025-guide-2eafb7c59d07),
  [Apple Intelligence Foundation Models 2025 (arXiv 2507.13575)](https://arxiv.org/pdf/2507.13575),
  [MiniCPM (arXiv 2404.06395)](https://arxiv.org/pdf/2404.06395) (on-device 4-bit quantised
  SLMs, ~2 GB, ~18 tok/s).
- [Vector Quantization — Tacnode](https://tacnode.io/post/vector-quantization-explained),
  [Why Vector Quantization Matters — MongoDB](https://www.mongodb.com/company/blog/innovation/why-vector-quantization-matters-for-ai-workloads),
  [Scaling Vector Search 80% cost cut — Towards Data Science](https://towardsdatascience.com/649627-2/)
  (binary ~24× / scalar ~4× storage, recall retention, rescore tiering).
- [AI Coach for Self-Improvement 2025](https://smartmindsociety.com/ai-coach-for-self-improvement-accelerating-personal-growth-in-2025/),
  [Best spaced repetition apps 2025 — Notionist](https://notionist.app/best-spaced-repetition-app)
  (daily-wins coaching, forgetting-curve resurfacing).
