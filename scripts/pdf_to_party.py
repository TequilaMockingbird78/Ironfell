#!/usr/bin/env python3
"""
Robust D&D 3.5e character-sheet PDF -> state/party.json

Supports:
  (A) Fillable PDFs via AcroForm fields (best if fields exist)
  (B) "Printed to PDF" / flattened PDFs *with selectable text* (your case)

Usage:
  python scripts/pdf_to_party.py --out state/party.json A_PRINTED.pdf B_PRINTED.pdf

Debug:
  python scripts/pdf_to_party.py --debug-text A_PRINTED.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader


# --------------------------
# Utilities
# --------------------------

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower()).strip()

def safe_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()

def safe_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x)
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None

def first_int(s: str) -> Optional[int]:
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None

def clean_spaces(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()

def read_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    parts: List[str] = []
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        if t:
            parts.append(t)
    return "\n".join(parts)

def read_pdf_fields(pdf_path: str) -> Dict[str, Any]:
    reader = PdfReader(pdf_path)
    fields: Dict[str, Any] = {}
    try:
        raw = reader.get_fields() or {}
        for k, v in raw.items():
            val = None
            if isinstance(v, dict):
                val = v.get("/V")
            else:
                val = getattr(v, "value", None)
                if val is None:
                    try:
                        val = v.get("/V")  # type: ignore
                    except Exception:
                        val = None
            fields[str(k)] = val
    except Exception:
        fields = {}
    return fields

def pick_field(fields: Dict[str, Any], candidates: List[str]) -> Optional[Any]:
    if not fields:
        return None
    norm_map = {norm(k): k for k in fields.keys()}
    for c in candidates:
        cn = norm(c)
        if cn in norm_map:
            return fields[norm_map[cn]]
        for nk, orig in norm_map.items():
            if cn and cn in nk:
                return fields[orig]
    return None


# --------------------------
# Printed-text parsing
# --------------------------

def find_line_value(text: str, label: str) -> str:
    """
    Finds a value that appears on the same line as a label.
    Example patterns seen in many character sheets:
      "Name Aedwen Marris"
      "Character Name: Aedwen Marris"
      "Deity St. Cuthbert"
    """
    # Look for: label ... value until end-of-line
    pat = re.compile(rf"(?im)^\s*{re.escape(label)}\s*[:\-]?\s*(.+?)\s*$")
    m = pat.search(text)
    if m:
        return clean_spaces(m.group(1))
    return ""

def find_block_after(text: str, header: str, stop_headers: List[str], max_lines: int = 40) -> str:
    """
    Extract a block of text after a header until another header is found or max_lines hit.
    Useful for feats/gear/spells blocks in printed sheets.
    """
    lines = text.splitlines()
    hdr_re = re.compile(rf"(?i)^\s*{re.escape(header)}\s*$")
    stop_res = [re.compile(rf"(?i)^\s*{re.escape(h)}\s*$") for h in stop_headers]

    for i, line in enumerate(lines):
        if hdr_re.match(line.strip()):
            out: List[str] = []
            for j in range(i + 1, min(i + 1 + max_lines, len(lines))):
                l2 = lines[j].strip()
                if any(sr.match(l2) for sr in stop_res):
                    break
                if l2:
                    out.append(l2)
            return "\n".join(out).strip()
    return ""

def parse_ability_scores(text: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Extract ability scores. Printed sheets usually include:
      STR 18 DEX 13 CON 17 INT 9 WIS 11 CHA 10
    We'll accept flexible spacing / line breaks.
    """
    scores = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}

    for abbr, key in [("STR", "str"), ("DEX", "dex"), ("CON", "con"), ("INT", "int"), ("WIS", "wis"), ("CHA", "cha")]:
        m = re.search(rf"(?i)\b{abbr}\b\s*[:\-]?\s*(-?\d+)\b", text)
        if m:
            scores[key] = int(m.group(1))

    mods = {k: (v - 10) // 2 for k, v in scores.items()}
    return scores, mods

def parse_ac(text: str) -> Dict[str, int]:
    # Total AC, Touch, Flat-Footed are often present as separate labeled values.
    total = first_int(find_line_value(text, "AC")) or first_int(find_line_value(text, "Armor Class")) or 10
    touch = first_int(find_line_value(text, "Touch")) or first_int(find_line_value(text, "Touch AC")) or 10
    flat = first_int(find_line_value(text, "Flat-Footed")) or first_int(find_line_value(text, "Flat Footed")) or 10
    return {"total": total, "touch": touch, "flat_footed": flat}

def parse_saves(text: str) -> Dict[str, int]:
    fort = first_int(find_line_value(text, "Fort")) or first_int(find_line_value(text, "Fortitude")) or 0
    ref = first_int(find_line_value(text, "Ref")) or first_int(find_line_value(text, "Reflex")) or 0
    will = first_int(find_line_value(text, "Will")) or 0
    return {"fort": fort, "ref": ref, "will": will}

def parse_basic_int(text: str, label_variants: List[str], default: int = 0) -> int:
    for lab in label_variants:
        v = first_int(find_line_value(text, lab))
        if v is not None:
            return v
    # fallback inline: "Speed 30" anywhere
    for lab in label_variants:
        m = re.search(rf"(?i)\b{re.escape(lab)}\b\s*[:\-]?\s*(-?\d+)\b", text)
        if m:
            return int(m.group(1))
    return default

def parse_weapons(text: str) -> List[Dict[str, str]]:
    """
    Printed sheets often list attacks in a table. Since table layouts vary,
    we use a forgiving heuristic:

    Look for lines that resemble:
      "Longsword +5 1d8+4 19-20/x2"
      "Light Crossbow +1 1d8 80 ft 19-20/x2"
      "Rapier +2 1d4+2 18-20/x2"
    """
    attacks: List[Dict[str, str]] = []
    for line in text.splitlines():
        l = clean_spaces(line)
        # quick filters
        if not l or len(l) < 8:
            continue
        # detect "name +X damage" pattern
        m = re.search(r"^([A-Za-z][A-Za-z0-9 '()/\-]+)\s+([+\-]?\d+)\s+(\d+d\d+(?:[+\-]\d+)?)", l)
        if not m:
            continue
        name = m.group(1).strip()
        to_hit = m.group(2).strip()
        dmg = m.group(3).strip()

        # try to locate crit (e.g., 19-20/x2 or x3)
        crit = ""
        mcrit = re.search(r"(\d{2}\s*-\s*\d{2}\s*/\s*x\d|x\d)", l, flags=re.IGNORECASE)
        if mcrit:
            crit = mcrit.group(1).replace(" ", "")

        # try to locate range (e.g., "80 ft", "110 ft")
        rng = ""
        mrng = re.search(r"(\d+)\s*ft", l, flags=re.IGNORECASE)
        if mrng:
            rng = f"{mrng.group(1)} ft"

        attacks.append({
            "name": name,
            "to_hit": int(to_hit),
            "damage": dmg,
            "critical": crit,
            "range": rng,
            "type": "",
            "notes": ""
        })

    # De-dup by name+to_hit+damage
    seen = set()
    out: List[Dict[str, str]] = []
    for a in attacks:
        key = (a["name"].lower(), a["to_hit"], a["damage"])
        if key in seen:
            continue
        seen.add(key)
        out.append(a)

    # Keep first few likely weapons (most sheets list 2–4)
    return out[:6]

def parse_listish_block(block: str) -> List[str]:
    if not block.strip():
        return []
    # split by commas or newlines
    parts = re.split(r"[,\n]+", block)
    out = []
    for p in parts:
        x = clean_spaces(p)
        if x:
            out.append(x)
    return out

def parse_skills(text: str, top_n: int = 6) -> List[Dict[str, int]]:
    """
    Skills tables are hard across PDFs. We do a simple heuristic:
    find lines like "Climb 6" or "Search 5" or "Craft (Alchemy) 3".
    """
    skills: List[Tuple[str, int]] = []
    for line in text.splitlines():
        l = clean_spaces(line)
        m = re.match(r"^([A-Za-z][A-Za-z '()\-]+)\s+([+\-]?\d+)$", l)
        if not m:
            continue
        name = m.group(1).strip()
        val = int(m.group(2))
        # avoid false positives
        if len(name) < 3:
            continue
        if name.lower() in {"ac", "hp", "bab"}:
            continue
        skills.append((name, val))

    # Prefer "real" skills by filtering common sheet fields
    blacklist = {"gender", "age", "height", "weight", "eyes", "hair", "skin", "size"}
    skills = [(n, v) for (n, v) in skills if n.lower() not in blacklist]

    # Sort by absolute bonus descending
    skills.sort(key=lambda t: abs(t[1]), reverse=True)
    out = [{"name": n, "bonus": v} for (n, v) in skills[:top_n]]
    return out

def parse_encumbrance(text: str) -> Optional[Dict[str, float]]:
    """
    If the printed sheet includes weights/loads. We look for:
      "Total Weight 69.5"
      "Light Load 100" etc.
    """
    total = None
    for lab in ["Total Weight", "Total Wt", "Weight Carried"]:
        v = find_line_value(text, lab)
        if v:
            try:
                total = float(re.search(r"(\d+(?:\.\d+)?)", v).group(1))  # type: ignore
                break
            except Exception:
                pass

    if total is None:
        return None

    light = parse_basic_int(text, ["Light Load"], default=0)
    medium = parse_basic_int(text, ["Medium Load"], default=0)
    heavy = parse_basic_int(text, ["Heavy Load"], default=0)
    return {"total_weight": total, "light_load": light, "medium_load": medium, "heavy_load": heavy}

def parse_printed_pc(text: str, source_pdf: str) -> Dict[str, Any]:
    # Identity
    name = find_line_value(text, "Character Name") or find_line_value(text, "Name")
    race = find_line_value(text, "Race")
    clazz = find_line_value(text, "Class")
    alignment = find_line_value(text, "Alignment")
    deity = find_line_value(text, "Deity") or find_line_value(text, "Patron")
    level = parse_basic_int(text, ["Level"], default=1)

    # Physical
    gender = find_line_value(text, "Gender") or find_line_value(text, "Sex")
    age = parse_basic_int(text, ["Age"], default=0) or None
    size = find_line_value(text, "Size")
    height = find_line_value(text, "Height")
    weight = find_line_value(text, "Weight")
    eyes = find_line_value(text, "Eyes")
    hair = find_line_value(text, "Hair")
    skin = find_line_value(text, "Skin")

    # Core stats
    ability_scores, ability_mods = parse_ability_scores(text)

    hp_cur = parse_basic_int(text, ["HP", "Hit Points", "Current HP"], default=0)
    hp_max = parse_basic_int(text, ["Max HP", "HP Max", "Total HP"], default=hp_cur)

    ac = parse_ac(text)
    speed = parse_basic_int(text, ["Speed", "Base Speed"], default=30)
    initiative = parse_basic_int(text, ["Initiative", "Init"], default=0)

    saves = parse_saves(text)
    bab = parse_basic_int(text, ["BAB", "Base Attack Bonus"], default=0)
    grapple = parse_basic_int(text, ["Grapple"], default=0)

    # Blocks that are often present as headings
    feats_block = find_block_after(
        text,
        header="Feats",
        stop_headers=["Special Abilities", "Equipment", "Gear", "Spells", "Spells Known", "Possessions"]
    )
    gear_block = find_block_after(
        text,
        header="Equipment",
        stop_headers=["Feats", "Special Abilities", "Spells", "Spells Known", "Possessions"]
    )
    if not gear_block:
        gear_block = find_block_after(
            text,
            header="Possessions",
            stop_headers=["Feats", "Special Abilities", "Spells", "Spells Known", "Equipment"]
        )

    feats = parse_listish_block(feats_block)
    gear = [clean_spaces(x) for x in gear_block.splitlines() if x.strip()]

    # Language lines vary; try some heuristics
    langs_line = find_line_value(text, "Languages")
    languages = parse_listish_block(langs_line)

    attacks = parse_weapons(text)
    skills = parse_skills(text, top_n=6)

    enc = parse_encumbrance(text)

    pc: Dict[str, Any] = {
        "name": name or os.path.splitext(os.path.basename(source_pdf))[0],
        "race": race,
        "class": clazz,
        "level": level,
        "alignment": alignment,
        "deity": deity,
        "background_hook": "Explorer on a Silent Canon mission",
        "personality_notes": "",
        "description": {
            "gender": gender,
            "age": age,
            "size": size,
            "height": height,
            "weight": weight,
            "eyes": eyes,
            "hair": hair,
            "skin": skin
        },
        "languages": languages,
        "ability_scores": ability_scores,
        "ability_mods": ability_mods,
        "hp": {"current": hp_cur, "max": hp_max},
        "ac": ac,
        "speed": speed,
        "initiative": initiative,
        "saves": saves,
        "base_attack_bonus": bab,
        "grapple": grapple,
        "attack_options": attacks,
        "skills_highlights": skills,
        "feats": feats,
        "special": [],
        "spells_prepared_or_known": [],
        "gear_highlights": gear,
        "limits": [],
        "source_pdf": os.path.basename(source_pdf)
    }

    if enc is not None:
        pc["encumbrance"] = enc

    return pc


# --------------------------
# Fillable-form parsing
# --------------------------

def parse_fillable_pc(fields: Dict[str, Any], source_pdf: str) -> Optional[Dict[str, Any]]:
    """
    If a PDF actually contains meaningful form fields, try to extract them.
    This is a best-effort helper; printed-text parsing is your reliable baseline.
    """
    if not fields:
        return None

    def f(*cands: str) -> str:
        v = pick_field(fields, list(cands))
        return safe_str(v) if v is not None else ""

    def fi(*cands: str) -> Optional[int]:
        v = pick_field(fields, list(cands))
        return safe_int(v) if v is not None else None

    name = f("Character Name", "Name", "PC Name")
    if not name:
        return None  # fields exist but not meaningful for our sheet

    race = f("Race")
    clazz = f("Class")
    alignment = f("Alignment")
    deity = f("Deity", "Patron")
    level = fi("Level") or 1

    # abilities
    ability_scores = {
        "str": fi("STR", "Strength") or 10,
        "dex": fi("DEX", "Dexterity") or 10,
        "con": fi("CON", "Constitution") or 10,
        "int": fi("INT", "Intelligence") or 10,
        "wis": fi("WIS", "Wisdom") or 10,
        "cha": fi("CHA", "Charisma") or 10
    }
    ability_mods = {k: (v - 10) // 2 for k, v in ability_scores.items()}

    hp_cur = fi("HP", "Hit Points") or 0
    hp_max = fi("Max HP", "Total HP") or hp_cur

    ac = {
        "total": fi("AC", "Armor Class") or 10,
        "touch": fi("Touch", "Touch AC") or 10,
        "flat_footed": fi("Flat-Footed", "Flat Footed") or 10
    }

    speed = fi("Speed") or 30
    initiative = fi("Initiative", "Init") or 0
    saves = {"fort": fi("Fort", "Fortitude") or 0, "ref": fi("Ref", "Reflex") or 0, "will": fi("Will") or 0}
    bab = fi("BAB", "Base Attack Bonus") or 0
    grapple = fi("Grapple") or 0

    pc = {
        "name": name,
        "race": race,
        "class": clazz,
        "level": level,
        "alignment": alignment,
        "deity": deity,
        "background_hook": "Explorer on a Silent Canon mission",
        "personality_notes": "",
        "description": {},
        "languages": parse_listish_block(f("Languages")),
        "ability_scores": ability_scores,
        "ability_mods": ability_mods,
        "hp": {"current": hp_cur, "max": hp_max},
        "ac": ac,
        "speed": speed,
        "initiative": initiative,
        "saves": saves,
        "base_attack_bonus": bab,
        "grapple": grapple,
        "attack_options": [],
        "skills_highlights": [],
        "feats": parse_listish_block(f("Feats")),
        "special": [],
        "spells_prepared_or_known": [],
        "gear_highlights": [],
        "limits": [],
        "source_pdf": os.path.basename(source_pdf)
    }
    return pc


# --------------------------
# Merge/write
# --------------------------

def load_party(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"party": []}

def merge_party(existing: Dict[str, Any], pcs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_name = {norm(p.get("name", "")): p for p in existing.get("party", [])}
    for p in pcs:
        by_name[norm(p.get("name", ""))] = p
    merged = {"party": list(by_name.values())}
    merged["party"].sort(key=lambda x: x.get("name", "").lower())
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="state/party.json", help="Output path")
    ap.add_argument("--debug-text", action="store_true", help="Print extracted text from PDFs and exit")
    ap.add_argument("pdfs", nargs="+", help="One or more PDFs")
    args = ap.parse_args()

    if args.debug_text:
        for p in args.pdfs:
            print(f"\n===== {p} =====")
            print(read_pdf_text(p))
        return

    pcs: List[Dict[str, Any]] = []

    for pdf in args.pdfs:
        fields = read_pdf_fields(pdf)
        fillable = parse_fillable_pc(fields, pdf)
        if fillable:
            pcs.append(fillable)
            continue

        text = read_pdf_text(pdf)
        if not text.strip():
            raise RuntimeError(f"No extractable text found in {pdf}. If this is an image scan, you'll need OCR.")
        pcs.append(parse_printed_pc(text, pdf))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    existing = load_party(args.out)
    merged = merge_party(existing, pcs)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(pcs)} character(s) into {args.out}")


if __name__ == "__main__":
    main()
