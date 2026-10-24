"""People registry and literal mention resolution, independent of the UI and LLM."""
import json
import re
import unicodedata
import uuid

from src import database


def normalize(value):
    return unicodedata.normalize("NFC", " ".join(str(value).split())).casefold()


def create_schema(conn):
    first_install = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='person_mentions'").fetchone() is None
    schema = """
        CREATE TABLE IF NOT EXISTS people (
            entity_id TEXT PRIMARY KEY REFERENCES entities(id),
            origin TEXT NOT NULL DEFAULT '', residence TEXT NOT NULL DEFAULT '',
            relationship TEXT NOT NULL DEFAULT '', known_from TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS person_aliases (
            person_id TEXT NOT NULL REFERENCES people(entity_id),
            alias TEXT NOT NULL, normalized TEXT NOT NULL,
            PRIMARY KEY(person_id, normalized));
        CREATE TABLE IF NOT EXISTS person_groups (
            person_id TEXT NOT NULL REFERENCES people(entity_id),
            label TEXT NOT NULL, normalized TEXT NOT NULL,
            PRIMARY KEY(person_id, normalized));
        CREATE TABLE IF NOT EXISTS person_mentions (
            entry_id TEXT NOT NULL REFERENCES diary_entries(id) ON DELETE CASCADE,
            mention TEXT NOT NULL, person_id TEXT REFERENCES people(entity_id),
            PRIMARY KEY(entry_id, mention));
        CREATE TABLE IF NOT EXISTS person_overrides (
            entry_id TEXT NOT NULL REFERENCES diary_entries(id) ON DELETE CASCADE,
            mention TEXT NOT NULL, person_id TEXT REFERENCES people(entity_id),
            PRIMARY KEY(entry_id, mention));
    """
    for statement in schema.split(";"):
        if statement.strip():
            conn.execute(statement)
    if first_install:
        conn.execute("""INSERT OR IGNORE INTO person_mentions(entry_id, mention)
            SELECT ee.entry_id, e.entity FROM entry_entities ee
            JOIN entities e ON e.id=ee.entity_id WHERE e.entity_type IN ('person', 'person_mention')""")
    if conn.execute("PRAGMA user_version").fetchone()[0] < 6:
        conn.execute("""UPDATE entities SET entity_type='person_mention'
            WHERE entity_type='person' AND id NOT IN (SELECT entity_id FROM people)""")
        for row in conn.execute("SELECT DISTINCT entry_id FROM person_mentions").fetchall():
            sync_entry_people(conn, row[0])


def list_people(conn=None):
    if conn is None:
        with database.conectar() as connection:
            return list_people(connection)
    result = []
    for row in conn.execute("""SELECT p.*, e.entity AS name FROM people p
        JOIN entities e ON e.id=p.entity_id ORDER BY e.entity COLLATE NOCASE, p.entity_id"""):
        item = dict(row)
        for table, key, field in (("person_aliases", "aliases", "alias"),
                                  ("person_groups", "groups", "label")):
            item[key] = [r[0] for r in conn.execute(
                f"SELECT {field} FROM {table} WHERE person_id=? ORDER BY normalized",
                (item["entity_id"],))]
        result.append(item)
    return result


def snapshot(people):
    return json.dumps(people, ensure_ascii=False, sort_keys=True)


def save_people(rows, expected):
    """Save the complete registry atomically; reject concurrent edits and deletions."""
    with database.conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = list_people(conn)
        if snapshot(existing) != expected:
            raise ValueError("Las personas han cambiado. Recarga la página antes de guardar.")
        old_ids = {p["entity_id"] for p in existing}
        if not old_ids.issubset({r.get("entity_id") for r in rows}):
            raise ValueError("Para conservar el diario, archiva las personas en lugar de eliminar filas.")
        names = set()
        labels = {normalize(g): g for p in existing for g in p["groups"]}
        for number, row in enumerate(rows, 1):
            name = " ".join(str(row.get("name") or "").split())
            if not name:
                raise ValueError(f"Falta el nombre completo en la fila {number}. Rellénalo o elimina esa fila si la añadiste por error.")
            if normalize(name) in names:
                raise ValueError(f'El nombre «{name}» está repetido en la fila {number}. Usa una sola ficha por persona; sus otras formas de nombre van en Alias.')
            names.add(normalize(name))
            person_id = row.get("entity_id")
            if person_id and person_id not in old_ids:
                raise ValueError("Identificador de persona desconocido.")
            if not person_id:
                person_id = str(uuid.uuid4())
                conn.execute("INSERT INTO entities VALUES(?, 'person', ?)", (person_id, name))
                conn.execute("INSERT INTO people(entity_id) VALUES(?)", (person_id,))
            else:
                conflict = conn.execute("SELECT id FROM entities WHERE entity_type='person' AND entity=? AND id<>?", (name, person_id)).fetchone()
                if conflict:
                    raise ValueError(f'Ya hay otra persona con el nombre «{name}». Usa un nombre distinto para cada ficha.')
                conn.execute("UPDATE entities SET entity=? WHERE id=?", (name, person_id))
            conn.execute("""UPDATE people SET origin=?, residence=?, relationship=?, known_from=?, archived=?
                WHERE entity_id=?""", tuple(str(row.get(k) or "").strip() for k in
                ("origin", "residence", "relationship", "known_from")) + (bool(row.get("archived")), person_id))
            for table, key, field in (("person_aliases", "aliases", "alias"), ("person_groups", "groups", "label")):
                conn.execute(f"DELETE FROM {table} WHERE person_id=?", (person_id,))
                for value in row.get(key, []):
                    value = " ".join(value.split())
                    if not value:
                        continue
                    normalized = normalize(value)
                    if key == "groups":
                        value = labels.setdefault(normalized, value)
                    conn.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?,?)", (person_id, value, normalized))
        conn.execute("DELETE FROM daily_reports")


def alias_index(conn):
    index = {}
    for person in list_people(conn):
        # Archived identities still reserve their aliases, preventing false unique matches.
        for alias in [person["name"], *person["aliases"]]:
            index.setdefault(normalize(alias), set()).add(person["entity_id"])
            if person["archived"]:
                index[normalize(alias)].add(None)
    return index


def literal_spans(text, index):
    """Return literal evidence positions in whitespace-normalized text."""
    source = unicodedata.normalize("NFC", " ".join(text.split()))
    matches = []
    for alias, ids in index.items():
        for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", source, re.IGNORECASE):
            matches.append((match.start(), match.end(), match.group(), ids))
    return matches


def select_mentions(matches):
    """Prefer explicit manual decisions, then the most specific expression."""
    chosen = []
    for start, end, alias, ids, priority in sorted(matches, key=lambda m: (-m[4], -(m[1]-m[0]), m[0])):
        if not any(start < b and end > a for a, b, _, _ in chosen):
            chosen.append((start, end, alias, ids))
    mentions = {}
    for _, _, alias, ids in chosen:
        key = normalize(alias)
        if key not in mentions:
            mentions[key] = (alias, set(ids))
        else:
            mentions[key][1].update(ids)
    return [(alias, next(iter(ids)) if len(ids) == 1 else None)
            for alias, ids in mentions.values()]


def find_mentions(text, index):
    return select_mentions([(*match, 0) for match in literal_spans(text, index)])


def contextual_spans(text, conn):
    """Only local, explicit profile statements; never infer from nearby places."""
    source = unicodedata.normalize("NFC", " ".join(text.split()))
    expressions = {}
    known_spans = literal_spans(text, alias_index(conn))
    for person in list_people(conn):
        ids = {person["entity_id"]}
        if person["archived"]:
            ids.add(None)
        for alias in [person["name"], *person["aliases"]]:
            name = "(?P<person>" + re.escape(normalize(alias)) + ")"
            relation = normalize(person["relationship"])
            # Free-form notes are not rules. Only a single relationship label is used.
            if re.fullmatch(r"[\wáéíóúüñ]+", relation):
                label = re.escape(relation)
                for pattern in (rf"mi {label} {name}", rf"{name},? mi {label}"):
                    expressions.setdefault(pattern, set()).update(ids)
            for field, phrase in (("residence", "vive en"), ("origin", "es de")):
                value = normalize(person[field])
                if value:
                    pattern = rf"{name},? (?:que )?{phrase} {re.escape(value)}"
                    expressions.setdefault(pattern, set()).update(ids)
    matches = []
    for pattern, ids in expressions.items():
        for match in re.finditer(r"(?<!\w)(?:" + pattern + r")(?!\w)", source, re.IGNORECASE):
            start, end = match.span("person")
            if any(a <= start and b >= end and b - a > end - start
                   for a, b, _, _ in known_spans):
                continue
            clause_prefix = re.split(r"[.!?;]", source[:match.start()])[-1].casefold()
            clause_end = re.search(r"[.!?;]", source[match.end():])
            if (re.search(r"\b(?:si|quizás|quizá|supongo|creo)\b", clause_prefix)
                    or (clause_end and clause_end.group() == "?")):
                continue
            matches.append((match.start(), match.end(), match.group(), ids, 0))
    # Two incompatible explicit clues about the same name require review.
    conflicts = set()
    for i, first in enumerate(matches):
        for j, second in enumerate(matches[:i]):
            if first[0] < second[1] and first[1] > second[0] and not first[3].intersection(second[3]):
                conflicts.update((i, j))
    matches = [(a, b, mention, {None} if i in conflicts else ids, priority)
               for i, (a, b, mention, ids, priority) in enumerate(matches)]
    return matches


def resolve_mentions(conn, text, extracted=(), overrides=(), *, context=True):
    """Reconcile all sources by evidence span before linking identities."""
    index = alias_index(conn)
    for mention in extracted:
        if mention.strip():
            index.setdefault(normalize(mention), {None})
    matches = [(*m, 0) for m in literal_spans(text, index)]
    if context:
        matches.extend(contextual_spans(text, conn))
    for row in overrides:
        matches.extend((*m, 1) for m in literal_spans(text, {normalize(row["mention"]): {row["person_id"]}}))
    return select_mentions(matches)


def matching_mention(conn, table, entry_id, mention):
    """Find a saved mention using the same normalization as alias matching."""
    if table not in ("person_mentions", "person_overrides"):
        raise ValueError("Tabla de menciones desconocida")
    return next((r for r in conn.execute(
        f"SELECT mention, person_id FROM {table} WHERE entry_id=?", (entry_id,))
        if normalize(r["mention"]) == normalize(mention)), None)


def link(conn, entry_id, mention, person_id):
    current = matching_mention(conn, "person_mentions", entry_id, mention)
    if current is not None:
        mention = current["mention"]
    conn.execute("INSERT INTO person_mentions VALUES(?,?,?) ON CONFLICT(entry_id,mention) DO UPDATE SET person_id=excluded.person_id",
                 (entry_id, mention, person_id))


def record_mentions(conn, entry_id, entities):
    conn.execute("DELETE FROM person_mentions WHERE entry_id=?", (entry_id,))
    text = conn.execute("SELECT transcription FROM diary_entries WHERE id=?", (entry_id,)).fetchone()[0]
    overrides = conn.execute("SELECT mention, person_id FROM person_overrides WHERE entry_id=?", (entry_id,)).fetchall()
    for row in overrides:
        if not find_mentions(text, {normalize(row["mention"]): {row["person_id"]}}):
            conn.execute("DELETE FROM person_overrides WHERE entry_id=? AND mention=?", (entry_id, row["mention"]))
    for mention, person_id in resolve_mentions(conn, text, [m for kind, m in entities if kind == "person"], overrides):
        link(conn, entry_id, mention, person_id)
    sync_entry_people(conn, entry_id)


def repair_overlapping_mentions(conn, entry_ids=None):
    """Repair legacy duplicate links without adding unreviewed historical identities."""
    for entry in conn.execute("SELECT id, transcription FROM diary_entries").fetchall():
        if entry_ids is not None and entry["id"] not in entry_ids:
            continue
        saved = conn.execute("SELECT mention, person_id FROM person_mentions WHERE entry_id=?", (entry["id"],)).fetchall()
        manual = conn.execute("SELECT mention, person_id FROM person_overrides WHERE entry_id=?", (entry["id"],)).fetchall()
        matches = [(*m, 0) for row in saved for m in literal_spans(entry["transcription"], {normalize(row["mention"]): {row["person_id"]}})]
        matches.extend((*m, 1) for row in manual for m in literal_spans(entry["transcription"], {normalize(row["mention"]): {row["person_id"]}}))
        conn.execute("DELETE FROM person_mentions WHERE entry_id=?", (entry["id"],))
        for mention, person_id in select_mentions(matches):
            link(conn, entry["id"], mention, person_id)
        sync_entry_people(conn, entry["id"])


def sync_entry_people(conn, entry_id):
    """Rebuild person links so corrections remove stale identities, preserving places."""
    conn.execute("DELETE FROM entry_entities WHERE entry_id=? AND entity_id IN (SELECT id FROM entities WHERE entity_type IN ('person', 'person_mention'))", (entry_id,))
    for row in conn.execute("SELECT mention, person_id FROM person_mentions WHERE entry_id=?", (entry_id,)).fetchall():
        entity_id = row["person_id"]
        if entity_id is None:
            conn.execute("INSERT INTO entities VALUES(?, 'person_mention', ?) ON CONFLICT(entity_type,entity) DO NOTHING", (str(uuid.uuid4()), row["mention"]))
            entity_id = conn.execute("SELECT id FROM entities WHERE entity_type='person_mention' AND entity=?", (row["mention"],)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO entry_entities VALUES(?,?)", (entry_id, entity_id))


def entry_people(entry_id, conn=None):
    if conn is None:
        with database.conectar() as connection:
            return entry_people(entry_id, connection)
    entry = conn.execute("SELECT transcription, status, processing FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
    if entry is None:
        raise ValueError("La entrada ya no existe.")
    mentions = [dict(r) for r in conn.execute("SELECT mention, person_id FROM person_mentions WHERE entry_id=? ORDER BY mention", (entry_id,))]
    return {"transcription": entry["transcription"], "status": entry["status"], "processing": entry["processing"], "mentions": mentions}


def correct_entry_people(entry_id, assignments, expected):
    """Manual choices apply to this entry only and survive analysis retries."""
    with database.conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = entry_people(entry_id, conn)
        if snapshot(state) != expected or state["processing"] or state["status"] not in ("completed", "failed", "empty"):
            raise ValueError("La entrada ha cambiado o se está procesando. Vuelve a abrir la edición de personas.")
        known = {m["mention"] for m in state["mentions"]}
        if not known.issubset(assignments):
            raise ValueError("Las menciones han cambiado. Vuelve a abrir la edición.")
        for mention in set(assignments) - known:
            if not mention.strip() or not find_mentions(state["transcription"], {normalize(mention): {None}}):
                raise ValueError("La mención añadida debe aparecer literalmente en la transcripción.")
        normalized_assignments = {}
        for mention, person_id in assignments.items():
            key = normalize(mention)
            if key in normalized_assignments and normalized_assignments[key] != person_id:
                raise ValueError("La misma mención tiene dos personas distintas. Unifica la selección de sus variantes.")
            normalized_assignments[key] = person_id
        valid_ids = {p["entity_id"] for p in list_people(conn)}
        previous = {m["mention"]: m["person_id"] for m in state["mentions"]}
        for mention, person_id in assignments.items():
            if person_id is not None and person_id not in valid_ids:
                raise ValueError("La persona seleccionada ya no existe.")
            if mention in previous and previous[mention] == person_id:
                continue
            for row in conn.execute("SELECT mention FROM person_overrides WHERE entry_id=?", (entry_id,)).fetchall():
                if normalize(row[0]) == normalize(mention):
                    conn.execute("DELETE FROM person_overrides WHERE entry_id=? AND mention=?", (entry_id, row[0]))
            conn.execute("INSERT INTO person_overrides VALUES(?,?,?) ON CONFLICT(entry_id,mention) DO UPDATE SET person_id=excluded.person_id", (entry_id, mention, person_id))
            link(conn, entry_id, mention, person_id)
        repair_overlapping_mentions(conn, {entry_id})
        conn.execute("DELETE FROM daily_reports")


def candidates():
    with database.conectar() as conn:
        return [dict(r) for r in conn.execute("""SELECT mention, count(*) AS entries_count
            FROM person_mentions WHERE person_id IS NULL GROUP BY mention ORDER BY mention""")]


def historical_proposals():
    with database.conectar() as conn:
        names = {p["entity_id"]: p["name"] for p in list_people(conn)}
        proposals = []
        for entry in conn.execute("SELECT id, transcription FROM diary_entries WHERE status='completed'"):
            saved = conn.execute("SELECT mention FROM person_mentions WHERE entry_id=?", (entry["id"],)).fetchall()
            manual = conn.execute("SELECT mention, person_id FROM person_overrides WHERE entry_id=?", (entry["id"],)).fetchall()
            for mention, person_id in resolve_mentions(conn, entry["transcription"], [r[0] for r in saved], manual):
                if not person_id:
                    continue
                if matching_mention(conn, "person_overrides", entry["id"], mention) is not None:
                    continue
                current = matching_mention(conn, "person_mentions", entry["id"], mention)
                if current is not None and current["person_id"] is not None:
                    continue
                proposals.append({"entry_id": entry["id"], "mention": mention,
                                  "person_id": person_id, "name": names[person_id],
                                  "text": entry["transcription"]})
        return proposals


def accept_proposals(proposals):
    with database.conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for proposal in proposals:
            entry_id, mention, person_id = (proposal[k] for k in ("entry_id", "mention", "person_id"))
            entry = conn.execute("SELECT transcription, status FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
            manual = conn.execute("SELECT mention, person_id FROM person_overrides WHERE entry_id=?", (entry_id,)).fetchall()
            resolved = dict(resolve_mentions(conn, entry["transcription"], overrides=manual)) if entry else {}
            if not entry or entry["status"] != "completed" or entry["transcription"] != proposal["text"] or resolved.get(mention) != person_id:
                raise ValueError("Las propuestas han cambiado. Vuelve a buscar coincidencias.")
            current = matching_mention(conn, "person_mentions", entry_id, mention)
            if matching_mention(conn, "person_overrides", entry_id, mention) is not None:
                raise ValueError("La mención se ha corregido manualmente. Vuelve a buscar coincidencias.")
            if current is not None and current["person_id"] not in (None, person_id):
                raise ValueError("La mención ya tiene otra persona asignada. Vuelve a buscar coincidencias.")
            link(conn, entry_id, mention, person_id)
            sync_entry_people(conn, entry_id)
        repair_overlapping_mentions(conn, {p["entry_id"] for p in proposals})
        conn.execute("DELETE FROM daily_reports")
