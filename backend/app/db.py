"""Metadata store: DynamoDB or a local JSON file with the same three tables
(documents, topics, reels)."""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .config import settings


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- local JSON
class LocalDB:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"documents": {}, "topics": {}, "reels": {}}))

    def _load(self) -> dict:
        return json.loads(self.path.read_text() or "{}")

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.path)

    # documents
    def put_document(self, doc: dict) -> None:
        with self.lock:
            d = self._load()
            d["documents"][doc["document_id"]] = doc
            self._save(d)

    def get_document(self, doc_id: str) -> Optional[dict]:
        with self.lock:
            return self._load()["documents"].get(doc_id)

    def update_document(self, doc_id: str, **fields) -> dict:
        with self.lock:
            d = self._load()
            doc = d["documents"].setdefault(doc_id, {"document_id": doc_id})
            doc.update(fields)
            doc["updated_at"] = now_iso()
            self._save(d)
            return doc

    def transition_document(self, doc_id: str, field: str, allowed: set, **fields) -> bool:
        """Set fields only if doc[field] is currently one of `allowed` (None = unset). Atomic."""
        with self.lock:
            d = self._load()
            doc = d["documents"].get(doc_id)
            if doc is None or doc.get(field) not in allowed:
                return False
            doc.update(fields)
            doc["updated_at"] = now_iso()
            self._save(d)
            return True

    def transition_reel(self, reel_id: str, field: str, allowed: set, **fields) -> bool:
        with self.lock:
            d = self._load()
            r = d["reels"].get(reel_id)
            if r is None or r.get(field) not in allowed:
                return False
            r.update(fields)
            r["updated_at"] = now_iso()
            self._save(d)
            return True

    def list_documents(self) -> list[dict]:
        with self.lock:
            docs = list(self._load()["documents"].values())
        return sorted(docs, key=lambda x: x.get("created_at", ""), reverse=True)

    # topics
    def put_topics(self, doc_id: str, topics: list[dict]) -> None:
        with self.lock:
            d = self._load()
            d["topics"] = {k: v for k, v in d["topics"].items() if v["document_id"] != doc_id}
            for t in topics:
                t["document_id"] = doc_id
                d["topics"][f"{doc_id}#{t['topic_id']}"] = t
            self._save(d)

    def list_topics(self, doc_id: str) -> list[dict]:
        with self.lock:
            ts = [t for t in self._load()["topics"].values() if t["document_id"] == doc_id]
        return sorted(ts, key=lambda t: t.get("learning_order", 0))

    # reels
    def put_reel(self, reel: dict) -> None:
        with self.lock:
            d = self._load()
            d["reels"][reel["reel_id"]] = reel
            self._save(d)

    def get_reel(self, reel_id: str) -> Optional[dict]:
        with self.lock:
            return self._load()["reels"].get(reel_id)

    def update_reel(self, reel_id: str, **fields) -> dict:
        with self.lock:
            d = self._load()
            r = d["reels"].setdefault(reel_id, {"reel_id": reel_id})
            r.update(fields)
            r["updated_at"] = now_iso()
            self._save(d)
            return r

    def list_reels(self, doc_id: str) -> list[dict]:
        with self.lock:
            rs = [r for r in self._load()["reels"].values() if r.get("document_id") == doc_id]
        return sorted(rs, key=lambda r: (r.get("learning_order", 0), r.get("part", 0)))

    def delete_reels(self, doc_id: str) -> None:
        with self.lock:
            d = self._load()
            d["reels"] = {k: v for k, v in d["reels"].items() if v.get("document_id") != doc_id}
            self._save(d)


# ---------------------------------------------------------------- DynamoDB
def _to_ddb(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    return obj


def _from_ddb(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_ddb(v) for v in obj]
    return obj


class DynamoDB:
    """Tables (create with scripts/aws_setup.py):
        documents: PK document_id
        topics:    PK document_id, SK topic_id
        reels:     PK reel_id, GSI by_document (document_id)
    """

    def __init__(self, region: str):
        import boto3
        from boto3.dynamodb.conditions import Key

        self.Key = Key
        ddb = boto3.resource("dynamodb", region_name=region)
        self.documents = ddb.Table(settings.DDB_TABLE_DOCUMENTS)
        self.topics = ddb.Table(settings.DDB_TABLE_TOPICS)
        self.reels = ddb.Table(settings.DDB_TABLE_REELS)

    @staticmethod
    def _update(table, key: dict, fields: dict) -> dict:
        fields = dict(fields, updated_at=now_iso())
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": _to_ddb(v) for i, v in enumerate(fields.values())}
        expr = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields)))
        r = table.update_item(
            Key=key, UpdateExpression=expr, ExpressionAttributeNames=names,
            ExpressionAttributeValues=values, ReturnValues="ALL_NEW",
        )
        return _from_ddb(r["Attributes"])

    def _transition(self, table, key: dict, field: str, allowed: set, fields: dict) -> bool:
        from botocore.exceptions import ClientError

        fields = dict(fields, updated_at=now_iso())
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        names["#c"] = field
        values = {f":v{i}": _to_ddb(v) for i, v in enumerate(fields.values())}
        conds = []
        vals = [a for a in allowed if a is not None]
        for i, a in enumerate(vals):
            values[f":a{i}"] = a
        if vals:
            conds.append("#c IN (" + ", ".join(f":a{i}" for i in range(len(vals))) + ")")
        if None in allowed:
            conds.append("attribute_not_exists(#c)")
        cond = "attribute_exists(" + next(iter(key)) + ") AND (" + " OR ".join(conds or ["attribute_exists(#c)"]) + ")"
        try:
            table.update_item(
                Key=key, UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
                ConditionExpression=cond, ExpressionAttributeNames=names, ExpressionAttributeValues=values,
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def transition_document(self, doc_id: str, field: str, allowed: set, **fields) -> bool:
        return self._transition(self.documents, {"document_id": doc_id}, field, allowed, fields)

    def transition_reel(self, reel_id: str, field: str, allowed: set, **fields) -> bool:
        return self._transition(self.reels, {"reel_id": reel_id}, field, allowed, fields)

    def put_document(self, doc: dict) -> None:
        self.documents.put_item(Item=_to_ddb(doc))

    def get_document(self, doc_id: str) -> Optional[dict]:
        r = self.documents.get_item(Key={"document_id": doc_id})
        return _from_ddb(r.get("Item"))

    def update_document(self, doc_id: str, **fields) -> dict:
        return self._update(self.documents, {"document_id": doc_id}, fields)

    def list_documents(self) -> list[dict]:
        items = _from_ddb(self.documents.scan(Limit=100).get("Items", []))
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    def put_topics(self, doc_id: str, topics: list[dict]) -> None:
        existing = self.list_topics(doc_id)
        new_ids = {t["topic_id"] for t in topics}
        # a single batch may not delete and put the same key, so delete stale rows first
        with self.topics.batch_writer() as bw:
            for t in existing:
                if t["topic_id"] not in new_ids:
                    bw.delete_item(Key={"document_id": doc_id, "topic_id": t["topic_id"]})
        with self.topics.batch_writer() as bw:
            for t in topics:
                t["document_id"] = doc_id
                bw.put_item(Item=_to_ddb(t))

    def list_topics(self, doc_id: str) -> list[dict]:
        r = self.topics.query(KeyConditionExpression=self.Key("document_id").eq(doc_id))
        return sorted(_from_ddb(r.get("Items", [])), key=lambda t: t.get("learning_order", 0))

    def put_reel(self, reel: dict) -> None:
        self.reels.put_item(Item=_to_ddb(reel))

    def get_reel(self, reel_id: str) -> Optional[dict]:
        return _from_ddb(self.reels.get_item(Key={"reel_id": reel_id}).get("Item"))

    def update_reel(self, reel_id: str, **fields) -> dict:
        return self._update(self.reels, {"reel_id": reel_id}, fields)

    def list_reels(self, doc_id: str) -> list[dict]:
        r = self.reels.query(
            IndexName="by_document", KeyConditionExpression=self.Key("document_id").eq(doc_id)
        )
        items = _from_ddb(r.get("Items", []))
        return sorted(items, key=lambda x: (x.get("learning_order", 0), x.get("part", 0)))

    def delete_reels(self, doc_id: str) -> None:
        with self.reels.batch_writer() as bw:
            for r in self.list_reels(doc_id):
                bw.delete_item(Key={"reel_id": r["reel_id"]})


def _build():
    if settings.DB == "dynamodb":
        return DynamoDB(settings.AWS_REGION)
    return LocalDB(settings.DATA_DIR / "kavach_db.json")


db = _build()
