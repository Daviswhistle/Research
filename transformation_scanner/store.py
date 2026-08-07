from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import EventCategory, RelatedEvent, ScoredCandidate


class ScannerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS disclosures (
                rcept_no TEXT PRIMARY KEY,
                corp_code TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                corp_name TEXT NOT NULL,
                report_nm TEXT NOT NULL,
                rcept_dt TEXT NOT NULL,
                category TEXT NOT NULL,
                subtype TEXT NOT NULL,
                classification_json TEXT NOT NULL,
                facts_json TEXT NOT NULL,
                disclosure_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_disclosures_corp_date
                ON disclosures(corp_code, rcept_dt);

            CREATE TABLE IF NOT EXISTS candidates (
                rcept_no TEXT PRIMARY KEY,
                attention_score REAL NOT NULL,
                financing_risk_score REAL NOT NULL,
                band TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(rcept_no) REFERENCES disclosures(rcept_no)
            );
            """
        )
        self._connection.commit()

    def seen_receipt(self, rcept_no: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM disclosures WHERE rcept_no = ? LIMIT 1",
            (str(rcept_no),),
        ).fetchone()
        return row is not None

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def save_candidate(self, candidate: ScoredCandidate) -> None:
        payload = candidate.to_dict()
        now = datetime.now(timezone.utc).isoformat()
        disclosure = candidate.disclosure
        classification = candidate.classification
        with self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO disclosures (
                    rcept_no, corp_code, stock_code, corp_name, report_nm, rcept_dt,
                    category, subtype, classification_json, facts_json,
                    disclosure_json, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT first_seen_at FROM disclosures WHERE rcept_no = ?), ?)
                )
                """,
                (
                    disclosure.rcept_no,
                    disclosure.corp_code,
                    disclosure.stock_code,
                    disclosure.corp_name,
                    disclosure.report_nm,
                    disclosure.rcept_dt,
                    classification.category.value,
                    classification.subtype,
                    self._json(classification.to_dict()),
                    self._json(dict(candidate.facts)),
                    self._json(disclosure.to_dict()),
                    disclosure.rcept_no,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT OR REPLACE INTO candidates (
                    rcept_no, attention_score, financing_risk_score, band,
                    candidate_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    disclosure.rcept_no,
                    candidate.attention_score,
                    candidate.financing_risk_score,
                    candidate.band.value,
                    self._json(payload),
                    now,
                ),
            )

    def recent_events(
        self,
        *,
        corp_code: str,
        start_date: str,
        end_date: str,
        exclude_receipt: str | None = None,
    ) -> list[RelatedEvent]:
        rows = self._connection.execute(
            """
            SELECT rcept_no, rcept_dt, category, subtype, facts_json
            FROM disclosures
            WHERE corp_code = ? AND rcept_dt BETWEEN ? AND ?
            ORDER BY rcept_dt ASC, rcept_no ASC
            """,
            (str(corp_code), str(start_date), str(end_date)),
        ).fetchall()
        events: list[RelatedEvent] = []
        for row in rows:
            if exclude_receipt and str(row["rcept_no"]) == str(exclude_receipt):
                continue
            try:
                category = EventCategory(str(row["category"]))
                facts = json.loads(str(row["facts_json"]) or "{}")
            except (ValueError, json.JSONDecodeError):
                continue
            events.append(
                RelatedEvent(
                    rcept_no=str(row["rcept_no"]),
                    rcept_dt=str(row["rcept_dt"]),
                    category=category,
                    subtype=str(row["subtype"]),
                    facts=facts if isinstance(facts, dict) else {},
                )
            )
        return events

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ScannerStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()
