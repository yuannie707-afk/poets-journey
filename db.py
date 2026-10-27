# -*- coding: utf-8 -*-
"""SQLite 存取层：一个人物一张表行，节点单独一张表。"""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(BASE, "data", "poets.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    dynasty TEXT DEFAULT '',
    bio     TEXT DEFAULT '',
    life    TEXT DEFAULT '',
    birth   INTEGER,
    death   INTEGER,
    color   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nodes (
    id        TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    year      INTEGER,
    year_text TEXT DEFAULT '',
    place     TEXT DEFAULT '',
    event     TEXT DEFAULT '',
    poem      TEXT DEFAULT '',
    approx    INTEGER DEFAULT 0,
    lng       REAL,
    lat       REAL,
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nodes_person ON nodes(person_id, year);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
    return DB_PATH


def row_person(row, nodes=None):
    doc = {
        "id": row["id"], "name": row["name"], "dynasty": row["dynasty"],
        "bio": row["bio"], "life": row["life"],
        "birth": row["birth"], "death": row["death"], "color": row["color"],
    }
    if nodes is not None:
        doc["nodes"] = nodes
    return doc


def row_node(row):
    node = {
        "id": row["id"], "year": row["year"], "yearText": row["year_text"],
        "place": row["place"], "event": row["event"], "poem": row["poem"],
    }
    if row["approx"]:
        node["approx"] = True
    if row["lng"] is not None and row["lat"] is not None:
        node["lng"], node["lat"] = row["lng"], row["lat"]
    return node


def get_person(pid, with_nodes=True):
    with connect() as conn:
        row = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        nodes = []
        if with_nodes:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE person_id=? ORDER BY year, id", (pid,)).fetchall()
            nodes = [row_node(r) for r in rows]
        return row_person(row, nodes if with_nodes else None)


def list_persons(order="name"):
    order_sql = {"name": "name", "count": "cnt DESC", "dynasty": "dynasty, name"}.get(order, "name")
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.*, (SELECT COUNT(*) FROM nodes n WHERE n.person_id = p.id) AS cnt
            FROM persons p ORDER BY %s""" % order_sql).fetchall()
        return [{
            "id": r["id"], "name": r["name"], "dynasty": r["dynasty"], "bio": r["bio"],
            "life": r["life"], "color": r["color"], "birth": r["birth"],
            "death": r["death"], "count": r["cnt"],
        } for r in rows]


def all_persons_full():
    with connect() as conn:
        persons = conn.execute("SELECT * FROM persons ORDER BY name").fetchall()
        out = []
        for p in persons:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE person_id=? ORDER BY year, id", (p["id"],)).fetchall()
            out.append(row_person(p, [row_node(r) for r in rows]))
        return out


def upsert_person(doc, overwrite=True):
    pid = doc["id"]
    exists = get_person(pid, with_nodes=False) is not None
    if exists and not overwrite:
        return "skipped"
    with connect() as conn:
        conn.execute("""
            INSERT INTO persons (id, name, dynasty, bio, life, birth, death, color)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, dynasty=excluded.dynasty, bio=excluded.bio,
              life=excluded.life, birth=excluded.birth, death=excluded.death, color=excluded.color
        """, (pid, doc.get("name", ""), doc.get("dynasty", ""), doc.get("bio", ""),
              doc.get("life", ""), doc.get("birth"), doc.get("death"), doc.get("color", "")))
        if overwrite:
            conn.execute("DELETE FROM nodes WHERE person_id=?", (pid,))
        for n in doc.get("nodes") or []:
            save_node(conn, pid, n)
    return "overwritten" if exists else "added"


def save_node(conn, pid, node):
    conn.execute("""
        INSERT INTO nodes (id, person_id, year, year_text, place, event, poem, approx, lng, lat)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          person_id=excluded.person_id, year=excluded.year, year_text=excluded.year_text,
          place=excluded.place, event=excluded.event, poem=excluded.poem,
          approx=excluded.approx, lng=excluded.lng, lat=excluded.lat
    """, (node["id"], pid, node.get("year"), node.get("yearText", ""), node.get("place", ""),
          node.get("event", ""), node.get("poem", ""), 1 if node.get("approx") else 0,
          node.get("lng"), node.get("lat")))


def update_person(pid, fields):
    allowed = [k for k in ("name", "dynasty", "bio", "life", "birth", "death", "color") if k in fields]
    if not allowed:
        return get_person(pid)
    with connect() as conn:
        conn.execute("UPDATE persons SET %s WHERE id=?" % ", ".join("%s=?" % k for k in allowed),
                     [fields[k] for k in allowed] + [pid])
    return get_person(pid)


def delete_person(pid):
    with connect() as conn:
        conn.execute("DELETE FROM nodes WHERE person_id=?", (pid,))
        cur = conn.execute("DELETE FROM persons WHERE id=?", (pid,))
        return cur.rowcount > 0


def next_node_id(conn, pid):
    rows = conn.execute("SELECT id FROM nodes WHERE person_id=?", (pid,)).fetchall()
    nums = []
    for r in rows:
        try:
            nums.append(int(str(r["id"]).rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            pass
    return "%s-%02d" % (pid, max(nums) + 1 if nums else 1)


def add_node(pid, node):
    with connect() as conn:
        if not node.get("id"):
            node["id"] = next_node_id(conn, pid)
        save_node(conn, pid, node)
        return row_node(conn.execute("SELECT * FROM nodes WHERE id=?", (node["id"],)).fetchone())


def find_node(node_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return None if not row else (row["person_id"], row_node(row))


def update_node(node_id, fields):
    found = find_node(node_id)
    if not found:
        return None
    map_ = {"year": "year", "yearText": "year_text", "place": "place", "event": "event",
            "poem": "poem", "lng": "lng", "lat": "lat"}
    sets, vals = [], []
    for k, col in map_.items():
        if k in fields:
            sets.append("%s=?" % col)
            vals.append(fields[k])
    if "approx" in fields:
        sets.append("approx=?")
        vals.append(1 if fields["approx"] else 0)
    if sets:
        with connect() as conn:
            conn.execute("UPDATE nodes SET %s WHERE id=?" % ", ".join(sets), vals + [node_id])
    return find_node(node_id)[1]


def delete_node(node_id):
    with connect() as conn:
        cur = conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        return cur.rowcount > 0


def stats():
    with connect() as conn:
        p = conn.execute("SELECT COUNT(*) c FROM persons").fetchone()["c"]
        n = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
    return p, n
