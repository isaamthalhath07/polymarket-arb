"""SQLite-backed cache for markets, embeddings, classifications, and arb snapshots."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
  condition_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  end_date TEXT,
  neg_risk INTEGER NOT NULL DEFAULT 0,
  rules_hash TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_id TEXT PRIMARY KEY,
  condition_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  volume_usd REAL NOT NULL DEFAULT 0,
  FOREIGN KEY(condition_id) REFERENCES markets(condition_id)
);
CREATE INDEX IF NOT EXISTS idx_tokens_cid ON tokens(condition_id);
CREATE TABLE IF NOT EXISTS embeddings (
  condition_id TEXT PRIMARY KEY,
  vector BLOB NOT NULL,
  title_hash TEXT NOT NULL,
  computed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS classifications (
  m1_cid TEXT NOT NULL,
  m2_cid TEXT NOT NULL,
  m1_rules_hash TEXT NOT NULL,
  m2_rules_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  dependent INTEGER NOT NULL,
  abstain INTEGER NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  classified_at INTEGER NOT NULL,
  PRIMARY KEY (m1_cid, m2_cid)
);
CREATE TABLE IF NOT EXISTS arb_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scanned_at INTEGER NOT NULL,
  m1_cid TEXT NOT NULL,
  m2_cid TEXT NOT NULL,
  subset_json TEXT NOT NULL,
  edge_per_unit REAL NOT NULL,
  max_units REAL NOT NULL,
  expected_profit_usd REAL NOT NULL,
  books_snapshot_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arb_scanned ON arb_snapshots(scanned_at DESC);
"""


class Cache:
    def __init__(self, path: str | Path = "scanner.db") -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def upsert_market(self, m: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO markets
               (condition_id, title, description, end_date, neg_risk, rules_hash, fetched_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m["condition_id"],
                m["title"],
                m["description"],
                m.get("end_date"),
                1 if m.get("neg_risk") else 0,
                m["rules_hash"],
                int(time.time()),
                json.dumps(m),
            ),
        )
        self.conn.execute("DELETE FROM tokens WHERE condition_id = ?", (m["condition_id"],))
        for cond in m["conditions"]:
            self.conn.execute(
                """INSERT OR REPLACE INTO tokens (token_id, condition_id, outcome, volume_usd)
                   VALUES (?, ?, ?, ?)""",
                (cond["id"], m["condition_id"], cond["question"], cond.get("volume_usd", 0.0)),
            )
        self.conn.commit()

    def get_market_rules_hash(self, condition_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT rules_hash FROM markets WHERE condition_id = ?", (condition_id,)
        ).fetchone()
        return row[0] if row else None

    def get_market(self, condition_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT raw_json FROM markets WHERE condition_id = ?", (condition_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def all_markets(self) -> list[dict]:
        rows = self.conn.execute("SELECT raw_json FROM markets").fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_embedding(self, condition_id: str, vector: np.ndarray, title_hash: str) -> None:
        v = np.asarray(vector, dtype=np.float32).tobytes()
        self.conn.execute(
            """INSERT OR REPLACE INTO embeddings (condition_id, vector, title_hash, computed_at)
               VALUES (?, ?, ?, ?)""",
            (condition_id, v, title_hash, int(time.time())),
        )
        self.conn.commit()

    def get_embedding(self, condition_id: str, expected_title_hash: str) -> np.ndarray | None:
        row = self.conn.execute(
            "SELECT vector, title_hash FROM embeddings WHERE condition_id = ?",
            (condition_id,),
        ).fetchone()
        if not row or row[1] != expected_title_hash:
            return None
        return np.frombuffer(row[0], dtype=np.float32)

    def get_classification(
        self, m1_cid: str, m2_cid: str, m1_rules_hash: str, m2_rules_hash: str
    ) -> dict | None:
        a, b = sorted((m1_cid, m2_cid))
        ah, bh = (m1_rules_hash, m2_rules_hash) if a == m1_cid else (m2_rules_hash, m1_rules_hash)
        row = self.conn.execute(
            """SELECT result_json, m1_rules_hash, m2_rules_hash FROM classifications
               WHERE m1_cid = ? AND m2_cid = ?""",
            (a, b),
        ).fetchone()
        if not row:
            return None
        if row[1] != ah or row[2] != bh:
            return None
        return json.loads(row[0])

    def save_classification(
        self,
        m1_cid: str,
        m2_cid: str,
        m1_rules_hash: str,
        m2_rules_hash: str,
        result: dict,
        cost_usd: float,
    ) -> None:
        a, b = sorted((m1_cid, m2_cid))
        ah, bh = (m1_rules_hash, m2_rules_hash) if a == m1_cid else (m2_rules_hash, m1_rules_hash)
        dependent = int(result.get("step4_dependence", {}).get("dependent", False))
        abstain = int(result.get("step5_self_check", {}).get("abstain", False))
        self.conn.execute(
            """INSERT OR REPLACE INTO classifications
               (m1_cid, m2_cid, m1_rules_hash, m2_rules_hash, result_json, dependent, abstain, cost_usd, classified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a, b, ah, bh, json.dumps(result), dependent, abstain, cost_usd, int(time.time())),
        )
        self.conn.commit()

    def save_arb_snapshot(
        self,
        m1_cid: str,
        m2_cid: str,
        subset: dict,
        edge_per_unit: float,
        max_units: float,
        expected_profit_usd: float,
        books_snapshot: dict,
    ) -> None:
        self.conn.execute(
            """INSERT INTO arb_snapshots
               (scanned_at, m1_cid, m2_cid, subset_json, edge_per_unit, max_units, expected_profit_usd, books_snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(time.time()),
                m1_cid,
                m2_cid,
                json.dumps(subset),
                edge_per_unit,
                max_units,
                expected_profit_usd,
                json.dumps(books_snapshot),
            ),
        )
        self.conn.commit()

    def dependent_pairs(self) -> Iterable[tuple[str, str, dict]]:
        rows = self.conn.execute(
            "SELECT m1_cid, m2_cid, result_json FROM classifications WHERE dependent = 1 AND abstain = 0"
        ).fetchall()
        for a, b, j in rows:
            yield a, b, json.loads(j)
