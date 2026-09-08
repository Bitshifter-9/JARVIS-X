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
- ✅ **#13 Personal phrasebook** — a "Your words" card lists the recurring names/jargon/acronyms
  mined from your own messages (deterministic, no model), the vocabulary the transcriber/drafter
  should never mishear.
- ✅ **#11 Personal style model** — already in place: a one-click style card from your sent mail
  (`learned_style`) that triage and drafting read so replies sound like you.
- ✅ **#16 Relationship cadence** — a "Reconnect?" card surfaces people you usually keep up with
  but have gone quiet on, from the rhythm of your correspondence (deterministic, no model).
- ✅ **#17 Interest drift** — a "Where your head is" card shows topics rising and fading in your
  own words (recent vs baseline), so briefings track the current you (deterministic, no model).
- ✅ **#25 "What matters now"** — a single ranked glance on the home dashboard that composes the
  urgent deadlines, promises due, replies owed and gone-quiet people into the few things to act on.
- ✅ **#15 Rhythm model** — a "Your rhythm" card: your energy curve by hour (focus sessions +
  completed tasks), with the peak hour and best window, so nudges can land when you're receptive.
- ✅ **#18 Mood trend / #12 Speech pattern / #29 Knowledge gaps** — an "About you" card: a
  private weekly sentiment line, the filler words and phrases you lean on, and the topics you
  keep asking about — all deterministic over your own words, no model, private to your account.
- ✅ **#39 Decision journal** — log a decision, your reasoning and what you expect; when its
  review date arrives it resurfaces to ask "did it work?" (worked/mixed/didn't), so you learn to
  decide better. A "Decisions" card on Insights.
- ✅ **#7 Clipboard history** — a searchable, capped history on the synced clipboard, tap to re-copy.
- ✅ **#50 Self-model export** — a portable, versioned bundle of everything learned about you
  (persona, style, phrasebook, speech, rhythm, relationships, knowledge, promises, decisions),
  exported from Settings. The roadmap's capstone — the seed of "a robot of myself."
- ✅ **#30 Time-travel + #36 Focus analytics** — reconstruct any past day (tasks, mail, notes,
  apps) from a date-picker on Insights; and a "Focus" card with deep-work vs distraction minutes
  and the apps that pull you away (over the opt-in activity samples). Deterministic.
- ✅ **#31 Habit coach + #27 Rediscover** — the Streaks card now coaches (celebrate a roll,
  smallest next step on a slip); and a "Rediscover" card surfaces an older note relevant to what
  you're doing right now. Both deterministic.
- ✅ **#19 Digital twin + #44 Auto-tasks + #40 Accountability + #3 usage timeline (audit)** —
  "Ask my twin" answers as you from the self-model; dated promises auto-spawn tracked tasks;
  an opt-in check-in nudges overdue promises once; and the Mac/phone usage timeline (macnode
  `--share-activity` + Android UsageStats) was already feeding the activity stream.
- ✅ **#35 Micro-lessons + #1 Screen memory** — tap a knowledge-gap chip for a 3-minute lesson;
  and `macnode --share-screen` OCRs the active window on-device (Vision), storing only the text
  as a life-searchable `screen` source (verify capture on your Mac). #46 cross-device already true.
- ✅ **#4 Reading/watching log** — `macnode --share-reading` logs the front browser tab (url +
  title) as a life-searchable `reading` source; the counterpart to screen memory for the web.
- ✅ **#5 Location trails + place learning** — a "Places" card labels home/work/frequent from
  coarse fixes (rounded to ~500 m server-side; context, not a map). Deterministic clustering.
- ✅ **#6 Media diary** — the reading/watching log becomes an annotatable diary: add "why it
  mattered" to what you watched/read and it turns into searchable knowledge.
- ✅ **#2 Ambient audio + #10 Meeting capture** — `macnode listen` / `macnode meeting` transcribe
  on the Mac (Whisper, audio discarded) and store only the text; a meeting's action items become
  tasks. Backend tested; Mac capture compile-checked (verify on your Mac).
- **Audit:** several items were already built and are now marked accurately — #32 weekly review,
  #33 behaviour/anomaly nudges, #34 goal-progress prediction (✅); #42 auto-triage, #47 hands-free
  voice, #49 smart-notification layer (🚧, core shipped, one piece each remaining).

Still open in the optimisation spine: binary-quantised vectors + full hot→warm→cold tiering —
deferred until the data volume makes them worth the complexity (the roadmap's own cheap-first rule).


---

## A. Ambient capture — the raw material (on-device, opt-in, privacy-gated)

1. ✅ **Screen memory (Mac).** `macnode run --share-screen` OCRs the active window on-device via
   the macOS Vision framework every 90 s and posts only the *text* (app + title + time) to
   `/v1/devices/{id}/screen` — the screenshot is deleted immediately; a stored `screen` source,
   30-day retention, read by life-search. Rewind-style recall, no disk cost. (Verify capture on
   your Mac; needs Screen Recording permission + pyobjc-framework-Vision.)
2. ✅ **Ambient audio → transcript.** `macnode listen` transcribes the room on this Mac with the
   existing faster-whisper and posts only the *text* to `/v1/devices/{id}/transcript` (a searchable
   `transcript` source) — the audio is transcribed locally and discarded. Consent-gated: explicit
   subcommand + a visible 🎙️ indicator, never silent. (Verify capture on your Mac; diarisation is next.)
3. ✅ **App & usage timeline, unified.** *Already built:* the Android `UsageStats` sampler **and**
   `macnode run --share-activity` both post the foreground app + title to `/v1/devices/{id}/activity`
   (titles only, 30-day retention), one cross-device stream that #36 Focus analytics reads.
4. ✅ **Reading & watching log.** `macnode run --share-reading` posts the front browser tab
   (Safari/Chrome — url + title, video/article/pdf) to `/v1/devices/{id}/reading` every 45 s, a
   life-searchable `reading` source (30 days, one row per url per hour) that feeds interest-drift
   (#17). (Auto-summarising the content is the later on-device step; verify capture on your Mac.)
5. ✅ **Location trails + place learning.** Coarse fixes are rounded to ~500 m *before storage*,
   then clustered and labelled by when you're there — home (nights), work (weekday days), or a
   frequent place. Context, never a map; 30-day retention, auto-pruned. A "Places" card on Insights
   with an explicit "Add current location" (continuous background capture via the FGS is next).
6. ✅ **Media diary.** What you watched and read (from the reading log #4), each with a one-line
   "why it mattered" you attach — and the takeaway folds into the searchable text, so consumption
   becomes recall-able knowledge. A "Media diary" card on Insights. `GET /v1/media-diary` + note.
7. ✅ **Clipboard history.** A searchable timeline of everything you copied, kept on the synced
   clipboard (capped, newest-first, tap to re-copy). A "History" view on the Devices clipboard card.
8. ⬜ **Photo & screenshot semantic index.** On-device caption + OCR every image once, store
   the caption, make your camera roll searchable ("that receipt from Goa").
9. ✅ **One-tap / voice quick-capture.** Hold-to-talk anywhere → transcribed, classified,
   filed — the frictionless inbox for a fleeting thought.
10. ✅ **Meeting & call capture.** `macnode meeting` transcribes a meeting on this Mac (Ctrl-C to
    end), saves the transcript, and the server turns its dated action items into tracked tasks.
    On-device Whisper, audio discarded. (Verify capture on your Mac.)

## B. The learning layer — becoming *you*

11. ✅ **Personal style model.** Learn your writing/speaking voice (sentence length, tone,
    emoji, sign-offs) so every draft sounds like you, not like a chatbot. *Already shipped:*
    `POST /v1/profile/learn-style` reads your sent mail on request and writes a style card to
    `profile.learned_style`, which triage/drafting already read. (A cheap always-on stats
    fallback from chat, and auto-refresh, are the later upgrades.)
12. ✅ **Speech-pattern profile.** The filler words you lean on, the phrases you repeat, your
    typical sentence length — from your own messages, deterministic, no model. Feeds
    drafting-in-your-voice and the coach. (Pace/prosody waits on ambient audio #2.)
13. ✅ **Personal phrasebook.** Your recurring jargon, names, and acronyms → feeds the
    transcriber and drafter so it stops mishearing "Guru Vai" as "guruvhy." Deterministic
    proper-noun/acronym frequency over your own words (chat + the profile you wrote), no model;
    a "Your words" card on Insights. (Wiring it into the transcriber/drafter is the next step.)
14. 🚧 **Preference learning from choices.** Every accept/reject/edit of a suggestion trains a
    lightweight ranker — the system's taste converges on yours. *Shipped:* dislike a reminder →
    its sender/channel is muted so it stops nagging (a "Muted" section on Goals holds the rules).
15. ✅ **Rhythm model.** When you focus, when you slump, your energy curve by hour/day —
    so JARVIS schedules and nudges *when you're actually receptive*. Deterministic: buckets your
    focus sessions and completed tasks by local hour over recent weeks → a 24-hour curve, peak
    hour and best window. A "Your rhythm" card on Insights. (Feeding it into nudge timing is next.)
16. ✅ **Deepened relationship graph.** Who matters, how you talk to them, your usual
    cadence — extends the existing people graph with contact intervals. Deterministic: groups
    your correspondents, reads the gaps between messages as a typical cadence, and flags anyone
    you usually keep up with but have gone quiet on. A "Reconnect?" card on Insights. (Phone/
    WhatsApp, where the person is in the notification title, is the later extension.)
17. ✅ **Interest drift model.** What you care about *now* vs. what you're drifting from, so
    briefings track your actual attention, not a stale profile. Deterministic: the topics/names
    in your own recent messages vs a longer baseline → rising and fading. A "Where your head is"
    card on Insights. Richer signal arrives once reading/watching capture (#4) lands.
18. ✅ **Private mood/sentiment trend.** From your own messages, a deterministic lexicon score
    by week — a gentle line, never shared, private to your account. Feeds the coach; surfaced in
    the "About you" card.
19. ✅ **Digital twin persona.** Answers *as you* — "what would I say?" — grounded on the self-model
    (your profile, style card, phrasebook and speech pattern) through the free/local cascade,
    degrading to "not enough of you yet" when signal is thin. `POST /v1/twin`; "Ask my twin" in
    Settings. The seed of the portable self-model.
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
25. ✅ **"What mattered" digest.** The few things that actually need you now, ranked into a
    single "What matters now" glance on the home dashboard — composes deadlines/promises due,
    replies owed, and who you've gone quiet on. Deterministic, no model; tap a row to jump there.
26. ✅ **One search box over your whole life.** Mail, chats, screens, notes, media — one
    semantic search, ranked, with time and source.
27. ✅ **Rediscover.** Surfaces an old idea/note relevant to what you're doing *right now* —
    from your current focus (the last thing you told JARVIS, or your top open task) it finds an
    older memory that matches and brings one back. A "Rediscover" card on Insights. Deterministic.
28. ✅ **Dropped-thread finder.** People who asked you something a few hours ago you may not
    have answered — read off the triage ``needs_reply`` classifications, one row per sender,
    muted senders excluded. An "Owe a reply?" card on Insights.
29. ✅ **Knowledge-gap detector.** Topics you keep asking about — the recurring subjects of your
    own questions — surfaced as likely gaps worth a micro-lesson (deterministic, no model).
30. ✅ **Time-travel reconstruction.** "What was I working on last Tuesday?" — a day rebuilt from
    everything that touched it: tasks finished, deadlines, mail/messages, notes, and the apps you
    spent time in. A date-picker in the Insights app bar opens the reconstruction. Deterministic.

## D. Self-improvement — the coach

31. ✅ **Habit coach.** Beyond the streak number: celebrates a roll, and offers the smallest next
    step when one has slipped ("you had a 5-day streak — one session restarts it"). Kind, never
    naggy, reads the streaks already computed. Shown on the Streaks card. Deterministic.
32. ✅ **Auto weekly self-review.** Wins, slips, one pattern, one focus for next week.
    *Already shipped:* `GET /v1/review/weekly` rolls up the past 7 days — done, slipped, focus
    minutes, where the time went, and what's due next week.
33. ✅ **Behaviour nudges.** "You doom-scroll after 11pm," "you reply to family late" —
    from the capture streams, kind not naggy. *Shipped:* the insight anomaly nudges + the
    heartbeat that surfaces them; richer behavioural signals grow as capture (#3/#4) lands.
34. ✅ **Skill & goal progress.** Milestones, trajectory, honest "on track / behind."
    *Already shipped:* the goal engine's prediction (`/goals/{id}/prediction`) — completion
    probability, critical path, and the fixes that would change the outcome.
35. ✅ **Personalised micro-lessons.** A gap you keep hitting (#29) → a crisp 3-minute lesson (a
    few points + one action) through the free/local cascade. Tap a "you keep asking about" chip on
    the About-you card. `GET /v1/micro-lesson`. (Spaced delivery is the later scheduling polish.)
36. ✅ **Focus analytics.** Deep-work vs distraction minutes, the apps that pull you away (ranked),
    and your best focus window — over the activity samples a device collected. A "Focus" card on
    Insights (empty until a device is sampling). Deterministic, no model.
37. ⬜ **Communication coach.** Your reply latency, tone drift, who you ghost — with concrete
    fixes.
38. ⬜ **Energy/health correlation.** If you connect sleep/steps, correlate them with your
    productivity so you learn what actually moves your day.
39. ✅ **Decision journal + outcome review.** Log a decision + your reasoning + what you expect;
    weeks later, when its review date arrives, JARVIS asks "did it work?" (worked / mixed / didn't)
    — so you learn to decide better. A "Decisions" card on Insights with a log sheet.
40. ✅ **Accountability mode.** Opt-in (`accountability_enabled`, a Settings toggle): once a promise
    from #22 is overdue past a grace window, the heartbeat checks in once — "did you do it?".
    Deterministic, gated, and never twice for the same promise.

## E. Full automation — hands-free on mobile + Mac

41. 🚧 **Autonomous morning brief & evening wind-down.** Run themselves; no prompt. *Shipped:* the
    heartbeat computes the morning brief every tick and alerts on newly at-risk goals, and the
    "What matters now" digest (#25) is the always-on brief. A scheduled push at set times is next.
42. 🚧 **Auto-triage everything.** Mail, messages, notifications → only the few that need
    *you* surface. *Shipped for mail:* triage classifies every message (needs_reply / fyi /
    deadline / spam / newsletter) and drafts the reply that's owed. Unifying messages and phone
    notifications into the same triage is the remaining piece.
43. 🚧 **Draft-in-your-voice, one-tap send.** Replies pre-written in your style (§B #11),
    queued for a single tap. *Shipped:* triage drafts the reply in your `learned_style` as a Gmail
    draft **and** queues the `gmail.send` as an approval — the one-tap send is approving it in the
    Approvals screen. A dedicated "pre-written replies" queue is the remaining polish.
44. ✅ **Auto-tasks from commitments.** A dated promise caught by #22 now spawns a linked task with no
    typing, so it enters the deadline/reminder machinery; "Coming up" defers to the task to avoid
    double-surfacing. Deterministic.
45. ⬜ **Overnight agent.** Within standing permissions, it tidies, follows up, and prepares
    while you sleep; every effectful step still auditable.
46. ✅ **True cross-device continuity.** Start on the Mac, finish on the phone — *already true:* one
    account, one shared brain (Postgres), signed device nodes; nothing syncs device-to-device, both
    read the same state. Life-search, memory, goals and the self-model are identical on either.
47. 🚧 **Fully hands-free voice mode.** Wake → understand → do → confirm, no screen.
    *Shipped:* the on-device wake loop (`wake_service.dart`) runs in a foreground service,
    listens for the wake word, sends the ask, and speaks the reply. Hardening it across every
    device state is the remaining work (needs on-device verification).
48. 🚧 **Scheduled autonomous workflows.** *Shipped:* the routines engine runs scheduled actions and
    the heartbeat runs periodic sweeps (memory prune, activity prune, commitment scan, learning);
    `/v1/review/weekly` is the weekly rollup. A user-defined workflow cron is the remaining piece.
49. 🚧 **Smart notification layer.** Batched, ranked, with a live-activity for the *one* thing
    that matters now. *Shipped:* the escalation ladder, the HUD live-activity, grouped-activity
    batching, and the "What matters now" ranking (#25). A single batched push digest is next.
50. ✅ **Self-model export (robot-ready).** A single portable, versioned bundle — persona, style,
    phrasebook, speech pattern, rhythm, interests, relationships, top knowledge, open promises and
    graded decisions — that a future embodied agent could load to *be* you. `GET /v1/self-model`,
    exported from Settings → Your data. No secrets. (Client-side encryption of the bundle is the
    next hardening step; today it's yours to keep, copied to your clipboard.)

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
