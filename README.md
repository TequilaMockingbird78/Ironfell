# RAG GM Sandbox (D&D 3.5e) — Zero-Day Campaign

This repository contains a small Retrieval-Augmented Generation (RAG) setup for running a Dungeons & Dragons 3.5e campaign as a “zero-day” exploration sandbox. The goal is to let a GM model generate new content while staying consistent with a growing canon, without pre-writing the entire world or spoiling discovery.

## Design Goals
- **Zero-day world:** the setting exists as structure and constraints, not a predetermined plot.
- **Replayable:** a new campaign can start with the same world scaffolding but different outcomes.
- **Consistent:** anything that becomes important can be promoted into canon (lore) and retrieved later.
- **Separation of concerns:** lore (world truth) is not mixed with rules, encounter tools, or per-campaign state.

---

## Repository Layout (State Zero)

### `lore/` — Canon world truth (ingested into the Lore vector DB)
This folder contains world facts and setting constraints that are intended to be retrieved by the GM model.
Typical contents:
- `lore/world/` — world indexes and region adjacency
- `lore/regions/` — region descriptions (geography, population, governance, norms)
- `lore/factions/` — faction definitions and scope
- `lore/government/` — governance structures
- `lore/magic/` — baseline magic norms
- `lore/religion/` — baseline religious norms
- `lore/routes/` — travel networks and route assumptions
- `lore/story_seeds/` — persistent, unresolved hooks that may reappear organically

**Important:** lore describes what *is true* in the setting. It should avoid probabilities, encounter math, or “must happen” plot beats.

### `rules/` — Mechanics reference (NOT lore; optionally ingested separately)
This contains compact markdown summaries of D&D 3.5e rules for tactical resolution.
- Kept separate to avoid treating mechanics as world canon.
- Intended for targeted retrieval only when player actions demand mechanics.

### `gm_guides/` (or `lore/gm_guides/`) — GM tools / palettes (soft-canon)
GM-facing guidance such as “encounter palettes” that list plausible ingredients (no probabilities).
These are consultative, not factual assertions.

### `scripts/` — Utilities and glue code
Automation and helper scripts, typically including:
- `ingest_lore.py` — ingests `lore/` into a Chroma collection (world canon)
- `smoke_test.py` — sanity check retrieval
- optional helpers (e.g., PDF-to-party state conversion)

### `state/` — Per-campaign state (NOT committed)
Holds current campaign state like party summaries and session snapshots, e.g.:
- `state/party.json` — party “dossier” used every session
- `state/session.json` — current location, chapter, time marker, notes

This directory changes constantly and should not be versioned for a clean “state zero.”

### `sessions/` or `journals/` — Play logs / chapter journals (NOT committed)
Contains outputs from play:
- chapter logs for narration
- optional public journal markdown

These are per-campaign artifacts and should not be committed by default.

### `chroma/` (or similar) — Vector DB persistence (NOT committed)
Local ChromaDB data. This is derived and environment-specific.

---

## What Gets Committed vs Not

### Commit
- `lore/` (canon setting)
- `rules/` (mechanics summaries)
- `gm_guides/` (GM tool palettes if used)
- `scripts/`
- `README.md`
- `.gitignore`

### Do Not Commit (default)
- `state/`
- `sessions/` / `journals/`
- `chroma/` (vector DB)
- secrets / API keys / local env files

---

## Workflow Summary
1. **Edit / add lore** under `lore/` as the world grows.
2. Run `scripts/ingest_lore.py` to re-index canon into the vector DB.
3. Maintain **party and campaign state** in `state/` (local).
4. During play, let the GM generate content; when something becomes important and recurring, **promote it** into `lore/story_seeds/` or other canon files.
5. Keep rules separate under `rules/` and retrieve them only when needed.

---

## Starting a New Campaign
- Start with a clean `state/` directory (or delete it).
- Create `state/party.json` for the new party.
- Set initial `state/session.json` (chapter/time/location).
- Begin Chapter 1 with your “start play” prompt (kept outside of lore).

---

## Notes
This repo intentionally avoids over-specification. The world expands only where the party travels, and canon is promoted only when it matters.
