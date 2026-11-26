# Speaker diarization: feasibility note

_2026-10-08 prototype. Scripts: [`diarization-prototype/`](diarization-prototype/)._

**Update 2026-10-09: removed.** After the batch-2026-10-09 audit below, `--diarize` was taken out of the pipeline. Its compute (about a quarter of the audio length) wasn't worth it for content-focused digests. This note stays as the record. The last version with the feature is the uncommitted working tree from 2026-10-09; it was never committed.

**Update 2026-10-09: implemented** as `digest.sh --diarize` (see README, "Speaker diarization"). Acceptance run on the Johnson video: all 12 anchor lines labeled correctly (host S1, Johnson S2; Johnson's second cluster survives as S3, 7.9 min). The gpt-6-luna report merged S2/S3 into Johnson itself, and every disputed attribution below came out right. One defect: the report's stated evidence for "S1 = George" cited timestamps (26:31, 27:54) where the name isn't spoken. The only direct address is at 29:09. The mapping was correct, but its evidence was invented, and the quote checker only checks blockquotes, so it wasn't caught.

## Why

whisper.cpp output has no speaker labels, so the analysis model guesses who said what. In the debt/gold/dollar batch, gpt-5.6 and gpt-6 disagreed about who made the passive-funds argument in the Brent Johnson interview, and gpt-6 contradicted itself between its per-video report and its roll-up. Neither run could be checked against the transcript.

## Options considered

| Option | Who it identifies | Cost to adopt | Verdict |
|---|---|---|---|
| whisper.cpp `--diarize` | Left vs. right stereo channel | None | Not useful. YouTube audio is effectively mono |
| whisper.cpp `--tinydiarize` | Only *where* turns change, not *which* speaker | Needs the `small.en-tdrz` model instead of `base.en`; experimental | Fallback only. It can't answer "which voice is Johnson" |
| **sherpa-onnx** (pyannote-3.0 segmentation + WeSpeaker ResNet34 embeddings, ONNX) | Clustered speakers S0, S1, … | `pip install sherpa-onnx soundfile numpy` (67 MB venv) + 33 MB of models from GitHub releases; no account, no torch. **Tested to install and import on Python 3.13 and 3.14** (pipeline's `python3`) | **Prototyped below. Works with caveats** |
| pyannote.audio (community-1) / WhisperX | Clustered speakers | Requires torch, a Hugging Face token, and accepting gated model terms. Size and accuracy are from memory, not measured here | Plan B. Needs the user to create the token |

## Prototype result: Brent Johnson interview (50 min, Apple M3, CPU)

`diarize.py` writes diarization turns to JSON. `merge.py` assigns each segment of the **existing** whisper SRT to the speaker with the most time overlap, then runs the anchor check. SRT segments are short (median 4.2 s, max 8.9 s; 679 segments), so no re-transcription is needed.

### Ground truth (lines whose speaker is fixed by their text)

| Speaker | Line | Auto-run cluster |
|---|---|---|
| Host | 0:00 "I'm back to my good buddy Brent Johnson" | S0 |
| Host | 5:28 "if I've learned one thing in doing these videos over the last five years and thousands of videos" | S0 |
| Host | 12:45 "I've learned a lot from Brent" | S0 |
| Host | 16:06 "obviously I'm not a professional or anything" | S0 |
| Host | 23:21 Rebel Capitalist Pro promo ("my good friends… Brent Johnson") | S0 |
| Host | 27:25 "your tail risk there Brent" | S0 |
| Host | 44:39 "a paper that you texted me about last night" | S0 |
| Host | 49:53 "Okay, fantastic Brent. Thanks for your time" | S0 |
| Johnson | 27:58 "I started this business in 99" | S2 |
| Johnson | 29:09 "Here's the other thing George" | S2 |
| Johnson | 44:49 "I just put it out today… a paper about how we think the Fed and the Treasury" | S2 |
| Johnson | 49:39 his firm's research URL | S2 |

`merge.py` checks six of these automatically. The others were read off the labeled transcript.

### Runs

| Run | Clusters | Anchor check | Runtime |
|---|---|---|---|
| A: auto speaker count (threshold 0.5), fp32 segmentation | 14: host = S0 (17 min); Johnson = S2 (22.7 min) **and** S4 (8 min); 11 fragments under 1 min each | **PASS**: every host anchor in S0, every Johnson anchor in S2 | 852 s (0.28× real time) |
| B: forced 2 speakers, int8 segmentation | S0 = 47.4 min, S1 = 1.3 min | **FAIL**: host and Johnson merged into S0 | 690 s (0.23× real time) |

In run A, S4 is Johnson by content continuity, not by an independent anchor. It finishes his sentences ("I started this business in 99" [S2] → "I came out into production… January of 2000" [S4]) and carries his Russian-reserves point.

Run B changed two settings at once (forced count and int8 segmentation), so the cause of its failure isn't isolated. The speed gain came from the int8 setting, which is not yet cleared. The next isolating run is auto count + int8.

### What it settles

All of the following are run A, cross-checked against the text anchors above.

- **5:28–7:24, host (S0):** the nominal-GDP framework: "when you want to figure out rates you got to start with nominal GDP," France at about 4.7% nominal GDP, the ~100 bp France–Germany nominal-GDP differential, "they have a nominal GDP problem."
- **7:24 and 8:37, Johnson (S2/S4):** Mike Green's passive-flow feedback loop, and the Bessent buyback example.
- **9:39, host (S0), one continuous block:** "I think I do… I totally get what Mike's saying, but I don't know if I would buy into the passive bid in treasuries," then "we're at 6.5 percent nominal GDP."
- **10:45, Johnson:** "Yeah, and I think you're right."
- **12:45, host:** the "what I really got wrong" story about rate-hiking cycles (5% Fed funds against 1% vs. 10% nominal GDP).

### How the two model runs compare to that

Files under `digests/debt-gold-dollar-batch-gpt56/` (gpt-5.6) and `digests/debt-gold-dollar-batch/` (gpt-6), Johnson video report:

| Claim | gpt-5.6 | gpt-6 | Diarization |
|---|---|---|---|
| Passive-flow argument | Under Johnson ✓ (line 101); but "unclear whether… human investors or mechanical trading" under George ✗ (line 86; Johnson said it, 9:33) | Per-video themes: "Johnson describes passive mandates" ✓; per-video claims list: under Host ✗; roll-up: "Report 3's host describes passive mandates… Johnson questions how much the passive bid matters" ✗ | Johnson |
| "Don't buy the passive bid in treasuries" + 6.5% nominal GDP | 6.5% under "George (uncertain)" ✓ (line 87); "passive management may be more important in equities than in Treasuries" under Johnson ✗ (line 107) | Listed under "Host" ✓; but the per-video report's critique says "Johnson himself questions how much the passive bid matters" ✗ | Host |
| Nominal GDP as the starting point (France 4.7%) | Under Brent Johnson: "Johnson says analysts should begin with nominal GDP" ✗ | Claims list under Host ✓; Johnson claims also include "Bond rates should be evaluated against nominal GDP" ✗; summary: "Johnson argues that higher yields can reflect nominal GDP" ✗ | Host |
| Rate-hiking-cycle mistake | "Johnson revises his earlier expectation…" ✗ | Under Johnson's claims ✗ | Host |

Neither model is reliable here, and the roll-ups inherit the errors. Both batch indexes present nominal GDP as **Johnson's** counter-framework ("Johnson: rates can remain high when nominal GDP is high"). By the transcript, it is mainly the **host's** framework, with Johnson agreeing ("I think that's a big part of it," 7:24).

## Caveats found

1. **Over-segmentation.** Auto clustering split Johnson into two clusters and produced 11 small fragment clusters containing a mix of both voices. An integration needs a cleanup step: fold clusters under ~2% of talk time into the neighboring turn's speaker, or re-cluster on per-turn embeddings (sherpa-onnx exposes `SpeakerEmbeddingExtractor`) with a tuned threshold. Whether a fixed speaker count helps is untested in isolation (see run B).
2. **Speed.** About 0.25× real time on the M3 CPU, so a 3-hour podcast would add about 45 minutes. Whisper's time wasn't measured for comparison. Diarization can run in parallel with whisper, and int8 or a lighter embedding model are worth benchmarking.
3. **Labels are anonymous.** S0, S2 and so on still have to be mapped to names. The LLM can do this from context (intros, the channel owner, "thanks, Brent"), but the prompt should require it to state its evidence for each mapping, or say "unidentified."
4. **One test file.** Two speakers, a clean studio recording, one ad read. Panels, phone-quality guests, and crosstalk are untested.

## Integration sketch (if approved)

- Opt-in `--diarize` flag (Discord: `diarize=yes`). Default off until it's been tested on more batches.
- Sidecar `<transcript>.speakers.json` next to the existing `.srt`. Cached SRTs stay valid, old batches can be diarized without re-transcribing, and the timestamps cited in existing reports don't change.
- Merge step: overlap assignment, fragment cleanup, then render the transcript the LLM sees with `[S0]` / `[S2]` turn prefixes.
- Prompt change: a "Speakers" section that maps labels to names with stated evidence.
- Optional dependencies in `requirements-diarize.txt`, installed into the same interpreter (3.14 verified); models fetched by `setup.sh --diarize`.
- Tests: merge assignment, fragment folding, and rendering, using a synthetic SRT and turn list. No model needed in the unit tests.

## Audit: batch-2026-10-09 (5 videos, `--diarize`, gpt-6-luna)

Each multi-speaker transcript was read in full against text anchors: self-introductions, direct address, ad reads, sign-offs. Each report's speaker attributions were then checked against the transcript. Every wrong attribution listed below was re-checked by hand.

| Video | Voices → labels | Label grade | Report errors |
|---|---|---|---|
| Doomberg, Raise Your Average (1h39m) | Doomberg = S1+S2; two hosts: S3 (unnamed), S4 = Mike Filbert | B | "Asymmetric cost of drones" is filed under Mike; S3 said it (01:13:40). The "back-channel" claim cites 45:00, but the line is at 55:21. The host intro "I'm Mike Filbert" (cold open) is labeled S2 (Doomberg); the report flags the conflict but leaves it unresolved |
| Doomberg, Commodity Culture (50m) | Mike/Doomberg = S1+S2, Jesse = S3 | B+ | None found (about 17 claims checked) |
| Peak Prosperity (58m) | Chris Martenson = S3 (+S1, S4, S5 fragments), Paul = S2 | B | Two of Paul's lines are filed under Chris: "absolute demand destruction… reacting to headlines" (S2, 04:47) and "trigger those algos" (S2, 24:59) |
| Kokinda, TFTC (1h02m) | Host = S1+S2, Kokinda = S3 | B− | Bessent's "global economic reordering" is credited to Kokinda; the host said it (S1, 55:03). The 0:00 cold open (Kokinda's voice) is labeled host |
| "Rickards" 90-Day Warning (26m) | One voice, S1 | — | Correctly called an "unidentified narrator". The script claims Rickards' biography in the first person ("For four decades, I have sat in the war rooms of the Pentagon") and keeps pushing subscriptions to the channel. Not Rickards. (One diarizer turn runs 830 s with no gap over 0.5 s. That fits a synthetic voice but also a music bed; untested.) The roll-up still lists its forecasts beside the real interviews |

**Where the remaining label errors come from.** Clustering is good: every audit found the main voices about 90–99% pure. Over-splitting (one person, two or three labels) cost nothing, because the model merged the splits (three reports) or hedged them and left out claims it couldn't attribute (Peak Prosperity). Nearly every mislabel sits at a turn edge, where one whisper segment holds both voices ("…wrap this thing up? Well, Jesse, first of all, great to be back with you" is one segment). These mixed segments are 2–9% of words per video. A segment gets one label, so no clustering setting can fix them. Splitting segments at turn boundaries needs word-level timestamps from whisper.
The second source is cold opens. A clip with music under it becomes its own fragment cluster and inherits a neighbor's label.

**Fill-rule experiment (rejected).** Labeling fragment-only segments with the nearest real turn in time fixed the Filbert intro and "Good to see you, Chris." It broke three Johnson lines, including the host's "So where do you land in that argument?" (20:32). Fragment clusters are mostly split-off pieces of whoever was just talking, so copy-previous stays.

**Where the report errors come from.** All four wrong attributions (drone cost, demand destruction, trigger algos, Bessent) sit on lines whose label was correct. The model filed a line under the wrong person while writing. Diarization removed the systematic flips seen before it, such as the whole nominal-GDP framework moving between speakers in the Johnson reports. What's left is about one slip per 20–30 claims. Cited timestamps can also miss: the back-channel claim by about ten minutes (45:00 cited, 55:21 actual), and the Bessent line by under a minute. A per-claim check could catch these: confirm that the label at the cited time maps to the claimed speaker, and that the cited text is actually there.

The roll-up (`index.md`) repeats none of the four wrong attributions. It credits Peak Prosperity points to "Martenson and Paul" jointly.
