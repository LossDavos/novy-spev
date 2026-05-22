#!/usr/bin/env python3
"""Normalize chord strings in song_parts for consistent transposition."""

import argparse
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime

DB_PATH = '/home/spevnik_admin/novy-spev/instance/songs.db'
BACKUP_DIR = '/home/spevnik_admin/novy-spev/instance/backups'
MAPPING_PATH = '/home/spevnik_admin/novy-spev/chord_mapping.json'

CHORD_PATTERN = re.compile(r"\[([^\]]+)\]")
ROOT_PATTERN = re.compile(r"^([A-Ha-h])([#b]?)(.*)$")


def normalize_part(part):
    text = part.strip()
    if not text:
        return None

    match = ROOT_PATTERN.match(text)
    if not match:
        return None

    letter, accidental, rest = match.groups()
    is_lower = letter.islower()
    letter = letter.upper()
    rest = rest.strip()
    rest_lower = rest.lower()

    # Normalize sus shortcuts
    if rest in ('2', '4'):
        rest = f"sus{rest}"
        rest_lower = rest.lower()
    if rest in ('7/4', '7\\4'):
        rest = '7sus4'
        rest_lower = rest.lower()

    # H system mapping (H = B natural, B = Bb)
    if letter == 'B':
        if accidental == '#':
            root = 'C'
            accidental = ''
        else:
            root = 'Bb'
            accidental = ''
    elif letter == 'H':
        if accidental == 'b':
            root = 'Bb'
            accidental = ''
        elif accidental == '#':
            root = 'C'
            accidental = ''
        else:
            root = 'H'
            accidental = ''
    else:
        root = letter

    root_str = f"{root}{accidental}"

    # Infer minor from lowercase root unless explicit quality is present
    if is_lower:
        if not rest_lower.startswith(('m', 'maj', 'dim', 'aug', 'sus', 'add')):
            rest = f"m{rest}"

    return f"{root_str}{rest}"


def normalize_chord(chord):
    raw = chord.strip()
    if not raw:
        return chord

    optional = raw.startswith('(') and raw.endswith(')') and len(raw) > 2
    inner = raw[1:-1].strip() if optional else raw

    inner = inner.replace('\\', '/')
    inner = re.sub(r"(?i)([A-Ha-h][#b]?)(m?7)/4", r"\1\2sus4", inner)

    parts = [p.strip() for p in inner.split('/')]
    normalized_parts = []
    for part in parts:
        if not part:
            return chord
        normalized = normalize_part(part)
        if normalized is None:
            return chord
        normalized_parts.append(normalized)

    normalized = '/'.join(normalized_parts)
    if optional:
        return f"({normalized})"
    return normalized


def build_mapping(cursor):
    unique = set()
    cursor.execute("SELECT song_parts FROM song")
    for (song_parts,) in cursor.fetchall():
        if not song_parts:
            continue
        try:
            parts = json.loads(song_parts)
        except Exception:
            continue
        for part in parts:
            lines = part.get('lines', []) if isinstance(part, dict) else []
            for line in lines:
                for chord in CHORD_PATTERN.findall(line or ''):
                    chord = chord.strip()
                    if chord:
                        unique.add(chord)

    mapping = {}
    for chord in sorted(unique, key=lambda s: (s.lower(), s)):
        mapping[chord] = normalize_chord(chord)

    return mapping


def replace_line(line, mapping):
    def repl(match):
        chord = match.group(1)
        replacement = mapping.get(chord, normalize_chord(chord))
        return f"[{replacement}]"

    return CHORD_PATTERN.sub(repl, line)


def main():
    parser = argparse.ArgumentParser(description="Normalize chord strings in song_parts.")
    parser.add_argument('--apply', action='store_true', help='Apply updates to the database.')
    parser.add_argument('--use-mapping', action='store_true', help='Use existing chord_mapping.json without regenerating.')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if args.use_mapping:
        if not os.path.exists(MAPPING_PATH):
            raise SystemExit(f"Mapping file not found: {MAPPING_PATH}")
        with open(MAPPING_PATH, 'r', encoding='utf-8') as handle:
            mapping = json.load(handle)
        print(f"Mapping loaded: {MAPPING_PATH}")
    else:
        mapping = build_mapping(cursor)
        with open(MAPPING_PATH, 'w', encoding='utf-8') as handle:
            json.dump(mapping, handle, ensure_ascii=False, indent=2)
        print(f"Mapping written: {MAPPING_PATH}")

    if not args.apply:
        print("Dry run only. No database changes were applied.")
        conn.close()
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f"songs_backup_before_chord_norm_{timestamp}.db")
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup created: {backup_path}")

    cursor.execute("SELECT id, song_parts FROM song")
    rows = cursor.fetchall()

    updated = 0
    for song_id, song_parts in rows:
        if not song_parts:
            continue
        try:
            parts = json.loads(song_parts)
        except Exception:
            continue

        changed = False
        for part in parts:
            if not isinstance(part, dict):
                continue
            lines = part.get('lines', [])
            new_lines = []
            for line in lines:
                if not isinstance(line, str):
                    new_lines.append(line)
                    continue
                new_line = replace_line(line, mapping)
                if new_line != line:
                    changed = True
                new_lines.append(new_line)
            part['lines'] = new_lines

        if changed:
            cursor.execute(
                "UPDATE song SET song_parts = ? WHERE id = ?",
                (json.dumps(parts, ensure_ascii=False), song_id),
            )
            updated += 1

    conn.commit()
    conn.close()

    print(f"Songs updated: {updated}")


if __name__ == '__main__':
    main()
