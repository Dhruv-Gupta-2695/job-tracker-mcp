"""
Owner-side access to the multi-user portal's data in Firestore, for the scheduled scan
(GitHub Actions) and the one-time Sheet import. It uses a Firebase service-account key from the
environment (FIREBASE_SERVICE_ACCOUNT_JSON, or FIREBASE_SERVICE_ACCOUNT_FILE for local runs) and
only ever touches users/{FIREBASE_OWNER_UID}. The key bypasses Firestore security rules, so it lives
only in a GitHub secret (or a local file kept out of git), never on the web server.
"""
from __future__ import annotations

import json
import os

MAX_SEEN_IDS = 3000


class FirestoreStore:
    def __init__(self, uid: str | None = None, client=None):
        self.uid = uid or os.environ.get("FIREBASE_OWNER_UID", "").strip()
        if not self.uid:
            raise RuntimeError("FIREBASE_OWNER_UID is not set.")
        self.db = client or self._connect()

    @staticmethod
    def _connect():
        from google.cloud import firestore
        from google.oauth2 import service_account
        raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
        if raw:
            info = json.loads(raw)
        elif path:
            with open(path) as f:
                info = json.load(f)
        else:
            raise RuntimeError("Set FIREBASE_SERVICE_ACCOUNT_JSON (or FIREBASE_SERVICE_ACCOUNT_FILE).")
        creds = service_account.Credentials.from_service_account_info(info)
        return firestore.Client(credentials=creds, project=info["project_id"])

    def _col(self, name: str):
        return self.db.collection("users").document(self.uid).collection(name)

    def load_apps(self) -> list[dict]:
        return [d.to_dict() for d in self._col("apps").stream()]

    def save_apps(self, apps: list[dict]) -> None:
        # Firestore batches hold at most 500 writes.
        for i in range(0, len(apps), 400):
            batch = self.db.batch()
            for a in apps[i:i + 400]:
                batch.set(self._col("apps").document(a["threadId"]), a, merge=True)
            batch.commit()

    def load_meta(self) -> dict:
        snap = self._col("meta").document("scan").get()
        data = snap.to_dict() if snap.exists else {}
        return {"lastScanAt": data.get("lastScanAt", ""), "seenIds": list(data.get("seenIds", []))}

    def save_meta(self, last_scan_at: str, seen_ids) -> None:
        ids = list(dict.fromkeys(seen_ids))[-MAX_SEEN_IDS:]
        self._col("meta").document("scan").set({"lastScanAt": last_scan_at, "seenIds": ids})
