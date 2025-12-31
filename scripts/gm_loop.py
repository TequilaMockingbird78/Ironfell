import json
import os
import re
from copy import deepcopy
from datetime import datetime

import chromadb
from openai import OpenAI

DB_PATH = "chroma_db"
STATE_PATH = "state/current.json"
SESSIONS_DIR = "sessions"
SNAPSHOT_DIR = os.path.join("state", "snapshots")
COLLECTION_NAME = "lore"
PUBLIC_DIR = "public_journal"
CHAPTERS_DIR = os.path.join(PUBLIC_DIR,"chapters")
CHAPTER_STATE_PATH = os.path.join("state", "chapter.json")

# This guy you should change if you want a higher quality response.
MODEL = "gpt-5-mini"  # working fine; swap later if you want
TOP_K = 6

SYSTEM_RULES = """
You are a D&D 3.5e game master assistant running an exploratory campaign.

Hard rules:
- Never invent player actions.
- Never contradict retrieved canon.
- The user provides player actions; you generate the world’s response as narration.
- DO NOT force consequences as “true” in a rules-lawyer way; present outcomes as narrated events and observations.
- If canon is missing, do not ask questions mid-response; make reasonable, labeled assumptions in ENGINE_NOTES only.

Output format (mandatory):
Return three sections in this exact order:

GM_NARRATION:
(Only the narration that a DM would read aloud.)

ENGINE_NOTES:
(Quiet notes: assumptions, canon citations, and what the engine thinks are the likely consequences. Not for the table.)

STATE_DELTA_JSON:
A fenced code block with ONLY valid JSON. Use a SMALL delta with only these keys (omit unused):
- time_advance: { "day": int?, "watch": "morning|afternoon|evening|night" }
- party_move: { "region_id": string?, "site_id": string|null? }
- discover: { "regions": [{region_id, party_name?}], "sites": [{site_id, party_name?, region_id?}], "factions": [{faction_id, party_name?}] }
- quests_add: [{id, title, status}]
- quests_update: [{id, status, notes?}]
- facts_add: [string]
- npcs_upsert: [{id, name?, role?, attitude?, location?}]
- notes_add: [string]
"""

def ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    os.makedirs(CHAPTERS_DIR, exist_ok=True)

def load_state():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def next_turn_number():
    """
    Determine next turn number based on existing session logs.
    Files look like: sessions/turn_0001.md
    """
    ensure_dirs()
    pat = re.compile(r"turn_(\d{4})\.md$")
    max_n = 0
    for fn in os.listdir(SESSIONS_DIR):
        m = pat.search(fn)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1

def snapshot(label="snapshot"):
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_\-]+", "_", label).strip("_") or "snapshot"
    out_path = os.path.join(SNAPSHOT_DIR, f"{ts}_{safe_label}.json")
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        state_text = f.read()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(state_text)
    print(f"[snapshot saved] {out_path}")

def retrieve_canon(query, k=TOP_K):
    client = chromadb.PersistentClient(path=DB_PATH)
    col = client.get_collection(COLLECTION_NAME)
    res = col.query(query_texts=[query], n_results=k)

    chunks = []
    for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
        tag = f"{meta.get('type','?')}:{meta.get('name','?')}:{meta.get('section','?')}"
        chunks.append(f"[{tag}]\n{doc}")
    return "\n\n".join(chunks)

def split_sections(text):
    """
    Extract GM_NARRATION and ENGINE_NOTES sections.
    """
    # non-greedy capture up to next header
    narr = ""
    notes = ""

    mn = re.search(r"GM_NARRATION:\s*(.*?)(?:\nENGINE_NOTES:|\Z)", text, re.DOTALL | re.IGNORECASE)
    if mn:
        narr = mn.group(1).strip()

    me = re.search(r"ENGINE_NOTES:\s*(.*?)(?:\nSTATE_DELTA_JSON:|\Z)", text, re.DOTALL | re.IGNORECASE)
    if me:
        notes = me.group(1).strip()

    return narr, notes

def extract_delta(text):
    """
    Extract JSON from a fenced block under STATE_DELTA_JSON.
    """
    m = re.search(r"STATE_DELTA_JSON:\s*```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if not m:
        raise ValueError("Could not find STATE_DELTA_JSON fenced JSON block.")
    return json.loads(m.group(1))

def apply_delta(state, delta):
    s = deepcopy(state)

    # time advance
    ta = delta.get("time_advance")
    if ta:
        s.setdefault("time", {})
        for key in ("day", "watch"):
            if key in ta and ta[key] is not None:
                s["time"][key] = ta[key]

    # party move
    pm = delta.get("party_move")
    if pm:
        s.setdefault("party", {}).setdefault("location", {})
        for key in ("region_id", "site_id"):
            if key in pm:
                s["party"]["location"][key] = pm[key]

    # discoveries
    disc = delta.get("discover", {})
    if disc:
        s.setdefault("discovered", {}).setdefault("regions", {})
        s.setdefault("discovered", {}).setdefault("sites", {})
        s.setdefault("discovered", {}).setdefault("factions", {})

        for r in disc.get("regions", []) or []:
            rid = r.get("region_id")
            if rid:
                s["discovered"]["regions"].setdefault(rid, {})
                if r.get("party_name"):
                    s["discovered"]["regions"][rid]["party_name"] = r["party_name"]

        for site in disc.get("sites", []) or []:
            sid = site.get("site_id")
            if sid:
                s["discovered"]["sites"].setdefault(sid, {})
                if site.get("party_name"):
                    s["discovered"]["sites"][sid]["party_name"] = site["party_name"]
                if site.get("region_id"):
                    s["discovered"]["sites"][sid]["region_id"] = site["region_id"]

        for f in disc.get("factions", []) or []:
            fid = f.get("faction_id")
            if fid:
                s["discovered"]["factions"].setdefault(fid, {})
                if f.get("party_name"):
                    s["discovered"]["factions"][fid]["party_name"] = f["party_name"]

    # quests
    if delta.get("quests_add"):
        s.setdefault("quests", [])
        existing_ids = {q.get("id") for q in s["quests"]}
        for q in delta["quests_add"]:
            if q.get("id") and q["id"] not in existing_ids:
                s["quests"].append(q)

    if delta.get("quests_update"):
        by_id = {q.get("id"): q for q in s.get("quests", []) if q.get("id")}
        for upd in delta["quests_update"]:
            qid = upd.get("id")
            if qid and qid in by_id:
                if "status" in upd:
                    by_id[qid]["status"] = upd["status"]
                if "notes" in upd and upd["notes"]:
                    by_id[qid].setdefault("notes", [])
                    by_id[qid]["notes"].append(upd["notes"])

    # facts
    if delta.get("facts_add"):
        s.setdefault("facts", [])
        for fact in delta["facts_add"]:
            if fact and fact not in s["facts"]:
                s["facts"].append(fact)

    # npcs
    if delta.get("npcs_upsert"):
        s.setdefault("npcs", {})
        for npc in delta["npcs_upsert"]:
            nid = npc.get("id")
            if not nid:
                continue
            s["npcs"].setdefault(nid, {})
            s["npcs"][nid].update({k: v for k, v in npc.items() if v is not None})

    # notes
    if delta.get("notes_add"):
        s.setdefault("notes", [])
        for n in delta["notes_add"]:
            if n:
                s["notes"].append(n)

    return s

def read_multiline_or_command():
    """
    Multi-line input mode:
    - User enters lines
    - blank line ends input
    Commands:
    - !snapshot <label>
    - !quit
    """
    print("\nEnter player actions / party intent (multi-line).")
    print("Finish with an empty line.")
    print("Commands: !snapshot <label> | !chapter start/add/compile/status/end | !quit\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            return "!quit"

        if not line.strip():
            break
        lines.append(line)

    if not lines:
        return ""  # no-op
    text = "\n".join(lines).strip()

    # single-line command support
    if text.startswith("!snapshot"):
        return text
    if text.startswith("!chapter"):
        return text
    if text.strip() == "!quit":
        return "!quit"

    return text

def write_session_log(turn_n, player_input, model_raw, narration, engine_notes, delta, canon_used):
    ensure_dirs()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(SESSIONS_DIR, f"turn_{turn_n:04d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Turn {turn_n:04d}\n")
        f.write(f"- Timestamp: {ts}\n")
        f.write(f"- Model: {MODEL}\n\n")

        f.write("## Player Input\n")
        f.write("```text\n")
        f.write(player_input.strip() + "\n")
        f.write("```\n\n")

        f.write("## GM Narration (shown at table)\n")
        f.write(narration.strip() + "\n\n")

        f.write("## ENGINE_NOTES (silent)\n")
        f.write(engine_notes.strip() + "\n\n")

        f.write("## STATE_DELTA_JSON (silent)\n")
        f.write("```json\n")
        f.write(json.dumps(delta, indent=2, ensure_ascii=False) if isinstance(delta, dict) else "{}")
        f.write("\n```\n\n")

        f.write("## CANON_USED (silent)\n")
        f.write("```text\n")
        f.write(canon_used.strip() + "\n")
        f.write("```\n\n")

        f.write("## RAW_MODEL_OUTPUT (silent)\n")
        f.write("```text\n")
        f.write(model_raw.strip() + "\n")
        f.write("```\n")

def load_chapter_state():
    ensure_dirs()
    if not os.path.exists(CHAPTER_STATE_PATH):
        return {"active": False, "slug": None, "title": None, "turns": []}
    with open(CHAPTER_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_chapter_state(ch):
    ensure_dirs()
    with open(CHAPTER_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(ch, f, indent=2, ensure_ascii=False)

def write_public_turn(turn_n, player_input, narration, state):
    """
    Write a clean, shareable turn log (no engine notes, no canon, no raw output).
    """
    ensure_dirs()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(PUBLIC_DIR, f"turn_{turn_n:04d}.md")

    # try to include a light header from state
    day = state.get("time", {}).get("day", None)
    watch = state.get("time", {}).get("watch", None)
    header_time = []
    if day is not None:
        header_time.append(f"Day {day}")
    if watch:
        header_time.append(str(watch).capitalize())
    when = " — ".join(header_time) if header_time else "—"

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Turn {turn_n:04d} {when}\n")
        f.write(f"- Timestamp: {ts}\n\n")

        f.write("## Party Input\n")
        f.write("```text\n")
        f.write(player_input.strip() + "\n")
        f.write("```\n\n")

        f.write("## Narration\n")
        f.write(narration.strip() + "\n")

def handle_chapter_command(cmd_text):
    """
    Commands:
      !chapter start <slug> "<Title>"
      !chapter compile
      !chapter status
    """
    ch = load_chapter_state()
    parts = cmd_text.split(maxsplit=2)

    if len(parts) < 2:
        print("Usage: !chapter start <slug> \"<Title>\" | !chapter compile | !chapter status")
        return

    sub = parts[1].strip().lower()

    if sub == "start":
        if len(parts) < 3:
            print("Usage: !chapter start <slug> \"<Title>\"")
            return
        # parts[2] contains: <slug> "<Title>" OR <slug> Title...
        # We'll parse first token as slug and remainder as title (optional quoted).
        rest = parts[2].strip()
        slug = rest.split(maxsplit=1)[0]
        title = rest[len(slug):].strip()

        # strip optional quotes
        if title.startswith('"') and title.endswith('"') and len(title) >= 2:
            title = title[1:-1].strip()
        if not title:
            title = slug.replace("_", " ").replace("-", " ").title()

        ch = {"active": True, "slug": slug, "title": title, "turns": []}
        save_chapter_state(ch)
        print(f"[chapter started] {slug} — {title}")
        return

    if sub == "status":
        if not ch.get("active"):
            print("[chapter] none active")
        else:
            print(f"[chapter active] {ch.get('slug')} — {ch.get('title')} (turns: {len(ch.get('turns', []))})")
        return

    if sub == "add":
        if not ch.get("active"):
            print("No active chapter. Start one with: !chapter start <slug> \"<Title>\"")
            return
        # actual adding happens after a turn is generated; we just acknowledge
        print("[chapter] next generated turn will be added automatically")
        save_chapter_state(ch)
        return

    if sub == "compile":
        if not ch.get("active"):
            print("No active chapter to compile.")
            return
        slug = ch.get("slug")
        title = ch.get("title")
        turns = ch.get("turns", [])

        out_path = os.path.join(CHAPTERS_DIR, f"{slug}.md")
        with open(out_path, "w", encoding="utf-8") as out:
            out.write(f"# {title}\n\n")
            out.write(f"_Compiled from turns: {', '.join([f'{t:04d}' for t in turns])}_\n\n")

            for t in turns:
                p = os.path.join(PUBLIC_DIR, f"turn_{t:04d}.md")
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        out.write(f.read().strip() + "\n\n---\n\n")
                else:
                    out.write(f"## Missing turn_{t:04d}.md\n\n---\n\n")

        print(f"[chapter compiled] {out_path}")
        return

    if sub == "end":
        if not ch.get("active"):
            print("No active chapter to end.")
            return

        # Optional: auto-compile on end (nice default)
        slug = ch.get("slug")
        title = ch.get("title")
        turns = ch.get("turns", [])

        if turns:
            out_path = os.path.join(CHAPTERS_DIR, f"{slug}.md")
            with open(out_path, "w", encoding="utf-8") as out:
                out.write(f"# {title}\n\n")
                out.write(f"_Compiled from turns: {', '.join([f'{t:04d}' for t in turns])}_\n\n")

                for t in turns:
                    p = os.path.join(PUBLIC_DIR, f"turn_{t:04d}.md")
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8") as f:
                            out.write(f.read().strip() + "\n\n---\n\n")
                    else:
                        out.write(f"## Missing turn_{t:04d}.md\n\n---\n\n")

            print(f"[chapter compiled] {out_path}")
        else:
            print("[chapter ended] (no turns were added)")

        # Clear chapter state
        ch = {"active": False, "slug": None, "title": None, "turns": []}
        save_chapter_state(ch)
        print("[chapter ended] ready for next chapter")
        return

    print("Unknown chapter command. Use: start|add|compile|status")

def main():
    ensure_dirs()
    client = OpenAI()

    print("GM loop ready. (Multi-line input; blank line to submit.)")

    while True:
        user_text = read_multiline_or_command()

        if user_text == "!quit":
            print("Bye.")
            return

        if user_text.startswith("!snapshot"):
            parts = user_text.split(maxsplit=1)
            label = parts[1] if len(parts) > 1 else "snapshot"
            snapshot(label)
            continue

        if user_text.startswith("!chapter"):
            handle_chapter_command(user_text)
            continue

        if not user_text:
            # no-op; continue loop
            continue

        # Load state fresh each turn
        state = load_state()

        # Retrieve canon based on the player's input
        canon = retrieve_canon(user_text)

        prompt = f"""
CANON CONTEXT:
{canon}

CURRENT STATE (JSON):
{json.dumps(state, indent=2, ensure_ascii=False)}

PLAYER ACTIONS / INTENT:
{user_text}
"""

        resp = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": prompt}
            ],
        )

        raw = resp.output_text
        narration, engine_notes = split_sections(raw)

        # Parse & apply delta silently
        delta = {}
        state_updated = False
        try:
            delta = extract_delta(raw)
            new_state = apply_delta(state, delta)
            save_state(new_state)
            state_updated = True
        except Exception:
            # We still proceed; state just won't update.
            delta = {}

        # Print ONLY narration to the table
        print("\n--- GM NARRATION ---\n")
        print(narration if narration else "(No GM_NARRATION found; check session log.)")
        print()

        # Write a session log (silent info preserved)
        # Determine turn number first
        turn_n = next_turn_number()

        # Write public journal turn (clean)
        write_public_turn(turn_n, user_text, narration, state)

        # Write private session log (full trace)
        write_session_log(
            turn_n=turn_n,
            player_input=user_text,
            model_raw=raw,
            narration=narration,
            engine_notes=engine_notes + ("\n\n[NOTE] State was NOT updated (delta parse failed)." if not state_updated else ""),
            delta=delta,
            canon_used=canon
        )

        # Chapter auto-add logic
        ch = load_chapter_state()
        if ch.get("active"):
            ch["turns"].append(turn_n)
            save_chapter_state(ch)

if __name__ == "__main__":
    main()
