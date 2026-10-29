"""Local game-account bindings for chat users."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserBinding:
    server: str
    account_id: int
    account_name: str


class UserBindingStore:
    """Map a chat user (platform id + sender id) to one game account."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_bindings (
                    user_key TEXT PRIMARY KEY,
                    server TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    account_name TEXT NOT NULL
                )
                """
            )
        os.chmod(path, 0o600)

    def get(self, user_key: str) -> UserBinding | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT server, account_id, account_name "
                "FROM user_bindings WHERE user_key = ?",
                (user_key,),
            ).fetchone()
        return UserBinding(*row) if row else None

    def set(self, user_key: str, binding: UserBinding) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT INTO user_bindings (user_key, server, account_id, account_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_key) DO UPDATE SET
                    server = excluded.server,
                    account_id = excluded.account_id,
                    account_name = excluded.account_name
                """,
                (user_key, binding.server, binding.account_id, binding.account_name),
            )
