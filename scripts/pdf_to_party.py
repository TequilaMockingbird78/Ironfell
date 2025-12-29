#!/usr/bin/env python3
"""
Convert D&D 3.5e character-sheet PDFs into a state/party.json entry.

Supports:
- Fillable PDFs (AcroForm fields) via pypdf (best case)
- Fallback: simple text scrape (optional pdfminer)

Usage:
  python scripts/pdf_to_party.py --out state/party.json /path/to/pc1.pdf /path/to/pc2.pdf

Tip:
  First run with --dump-fields to see the PDF field names:
  python scripts/pdf_to_party.py --dump-fields A_v2.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader


def _normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower()).strip()


def _safe_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip()
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None


def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def _read_pdf_form_fields(pdf_path: str) -> Dict[str, Any]:
    """
    Returns a dict of AcroForm field_name -> value for a fillable PDF.
    """
    reader = PdfReader(pdf_path)

    fields: Dict[str, Any] = {}
    try:
        raw_fields = reader.get_fields() or {}
        for k, v in raw_fields.items():
            # v is usually a Field object or dict-like with /V
            val = None
            if isinstance(v, dict):
                val = v.get("/V")
            else:
                # pypdf Field might expose .value, but not guaranteed
                val = getattr(v, "value", None)
                if val is None and hasattr(v, "__getitem__"):
                    try:
                        val = v.get("/V")
                    except Exception:
                        val = None
            fields[str(k)] = val
    except Exception:
        # Not a fillable PDF or pypdf can't read fields
        fields = {}

    # Also try annotations (some forms store values there)
    if not fields:
        try:
            for page in reader.pages:
                annots = page.get("/Annots", [])
                for a in annots:
                    obj = a.get_object()
                    t = obj.get("/T")
                    v = obj.get("/V")
                    if t:
                        fields[str(t)] = v
        except Exception:
            pass

    return fields


def _extract_text_fallback(pdf_path: str) -> str:
    """
    Simple fallback text extraction using pypdf (works for many PDFs, but not all).
    If you need stronger extraction, install pdfminer.six and wire it in.
    """
    reader = PdfReader(pdf_path)
    parts: List[str] = []
    for p in reader.pages:
        try:
            txt = p.extract_text() or ""
        except Exception:
            txt = ""
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def _pick_field(fields: Dict[str, Any], candidates: List[str]) -> Optional[Any]:
    """
    Try matching by normalized field name containing candidate tokens.
    candidates: list of tokens in preference order (e.g. ["charactername", "name"])
    """
    if not fields:
        return None

    norm_map = {_normalize_key(k): k for k in fields.keys()}

    # Direct or substring match
    for c in candidates:
        cn = _normalize_key(c)
        # exact
        if cn in norm_map:
            return fields[norm_map[cn]]
        # substring
        for nk, orig in norm_map.items():
            if cn in nk:
                return fields[orig]
    return None


def _best_effort_parse_pc(pdf_path: str) -> Dict[str, Any]:
    """
    Heuristic mapping. This will vary by character-sheet template.
    You can refine the candidates once you know your field names via --dump-fields.
    """
    fields = _read_pdf_form_fields(pdf_path)
    text = "" if fields else _extract_text_fallback(pdf_path)

    def f(*cand: str) -> str:
        v = _pick_field(fields, list(cand))
        if v is not None:
            return _safe_str(v)
        # fallback regex on text
        for c in cand:
            # crude: "Label: value"
            pat = rf"{re.escape(c)}\s*[:\-]\s*(.+)"
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    def fi(*cand: str) -> Optional[int]:
        v = _pick_field(fields, list(cand))
        if v is not None:
            return _safe_int(v)
        for c in cand:
            pat = rf"{re.escape(c)}\s*[:\-]\s*(-?\d+)"
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None

    # Core identity
    name = f("Character Name", "Name", "PC Name", "charactername")
    race = f("Race", "race")
    clazz = f("Class", "class")
    level = fi("Level", "level") or 1
    alignment = f("Alignment", "alignment")
    deity = f("Deity", "Patron", "deity")

    # Abilities (scores)
    str_score = fi("STR", "Strength")
    dex_score = fi("DEX", "Dexterity")
    con_score = fi("CON", "Constitution")
    int_score = fi("INT", "Intelligence")
    wis_score = fi("WIS", "Wisdom")
    cha_score = fi("CHA", "Charisma")

    # Derived
    hp = fi("HP", "Hit Points", "Current HP") or 0
    hp_max = fi("Max HP", "HP Max", "Total HP") or hp

    ac_total = fi("AC", "Armor Class") or 10
    touch = fi("Touch", "Touch AC") or 10
    flat = fi("Flat-Footed", "Flat Footed", "Flat-Footed AC") or 10

    speed = fi("Speed", "Base Speed") or 30
    init = fi("Initiative", "Init") or 0

    fort = fi("Fort", "Fortitude") or 0
    ref = fi("Ref", "Reflex") or 0
    will = fi("Will") or 0

    bab = fi("BAB", "Base Attack Bonus") or 0
    grapple = fi("Grapple")  # optional

    # Optional physical descriptors (best-effort)
    gender = f("Gender", "Sex")
    age = fi("Age")
    height = f("Height")
    weight = f("Weight")
    eyes = f("Eyes")
    hair = f("Hair")
    skin = f("Skin")
    size = f("Size")

    # Feats / Special / Spells (sheet-dependent; we keep them empty unless detected)
    feats_raw = f("Feats", "Feat")
    feats = [s.strip() for s in re.split(r"[,\n]+", feats_raw) if s.strip()] if feats_raw else []

    spells_raw = f("Spells", "Spells Known", "Prepared Spells")
    spells = [s.strip() for s in re.split(r"[,\n]+", spells_raw) if s.strip()] if spells_raw else []

    # Minimal attacks (often need template-specific field names; leave empty if unknown)
    # You can extend candidates once you see your sheet field names.
    attack_name = f("Weapon", "Attack", "Weapon 1", "weapon")
    attack_bonus = fi("Attack Bonus", "To Hit", "Atk Bonus", "attackbonus") or 0
    attack_dmg = f("Damage", "Dmg", "damage") or ""

    attacks = []
    if attack_name or attack_dmg:
        attacks.append(
            {
                "name": attack_name or "Weapon",
                "to_hit": attack_bonus,
                "damage": attack_dmg or "",
                "critical": "",
                "range": "",
                "type": "",
                "notes": ""
            }
        )

    pc: Dict[str, Any] = {
        "name": name or os.path.splitext(os.path.basename(pdf_path))[0],
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
            "skin": skin,
        },
        "languages": [],
        "ability_scores": {
            "str": str_score if str_score is not None else 10,
            "dex": dex_score if dex_score is not None else 10,
            "con": con_score if con_score is not None else 10,
            "int": int_score if int_score is not None else 10,
            "wis": wis_score if wis_score is not None else 10,
            "cha": cha_score if cha_score is not None else 10,
        },
        "hp": {"current": hp, "max": hp_max},
        "ac": {"total": ac_total, "touch": touch, "flat_footed": flat},
        "speed": speed,
        "initiative": init,
        "saves": {"fort": fort, "ref": ref, "will": will},
        "base_attack_bonus": bab,
        "grapple": grapple if grapple is not None else None,
        "attack_options": attacks,
        "skills_highlights": [],
        "feats": feats,
        "special": [],
        "spells_prepared_or_known": spells,
        "gear_highlights": [],
        "limits": [],
        "source_pdf": os.path.basename(pdf_path),
    }

    # Clean None grapple key (optional)
    if pc.get("grapple") is None:
        pc.pop("grapple", None)

    return pc


def _load_party(out_path: str) -> Dict[str, Any]:
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"party": []}


def _merge_party(existing: Dict[str, Any], new_pcs: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Merge by name (case-insensitive); replace if same
    by_name = {_normalize_key(pc.get("name", "")): pc for pc in existing.get("party", [])}
    for pc in new_pcs:
        by_name[_normalize_key(pc.get("name", ""))] = pc
    merged = {"party": list(by_name.values())}
    # stable-ish ordering by name
    merged["party"].sort(key=lambda x: x.get("name", "").lower())
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="state/party.json", help="Output party.json path")
    ap.add_argument("--dump-fields", action="store_true", help="Print PDF field names and quit")
    ap.add_argument("pdfs", nargs="+", help="One or more character sheet PDFs")
    args = ap.parse_args()

    if args.dump_fields:
        for p in args.pdfs:
            fields = _read_pdf_form_fields(p)
            print(f"\n== {p} ==")
            if not fields:
                print("No form fields detected (may be flattened).")
                continue
            for k in sorted(fields.keys(), key=lambda s: s.lower()):
                print(f"- {k}")
        return

    pcs: List[Dict[str, Any]] = []
    for p in args.pdfs:
        pc = _best_effort_parse_pc(p)
        pcs.append(pc)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    existing = _load_party(args.out)
    merged = _merge_party(existing, pcs)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(pcs)} PC(s) into {args.out}")
    print("Tip: run with --dump-fields if any values are missing and you want to refine mapping.")


if __name__ == "__main__":
    main()
