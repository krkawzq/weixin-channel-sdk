"""Local token and cursor storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import AccountSession
from .utils import default_state_dir, safe_key


class StateStore:
    """Filesystem-backed state store.

    Defaults to `~/.weixin-channel`. Token/session files are chmod'd to 0600
    on platforms that support it.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_state_dir()

    @property
    def session_path(self) -> Path:
        return self.root / "account-session.json"

    @property
    def accounts_dir(self) -> Path:
        return self.root / "accounts"

    @property
    def account_index_path(self) -> Path:
        return self.root / "accounts.json"

    def account_session_path(self, account_id: str) -> Path:
        return self.accounts_dir / f"{safe_key(account_id)}.json"

    def cursor_path(self, account_id: str | None = None) -> Path:
        key = safe_key(account_id or "default")
        return self.root / f"{key}.cursor.json"

    def seen_path(self, account_id: str | None = None) -> Path:
        key = safe_key(account_id or "default")
        return self.root / f"{key}.seen.json"

    def pause_path(self, account_id: str | None = None) -> Path:
        key = safe_key(account_id or "default")
        return self.root / f"{key}.pause.json"

    def save_session(self, session: AccountSession) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_session_file(self.session_path, session)
        if session.account_id:
            self.accounts_dir.mkdir(parents=True, exist_ok=True)
            self._write_session_file(self.account_session_path(session.account_id), session)
            self._add_account_id(session.account_id)

    def _write_session_file(self, path: Path, session: AccountSession) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, session.model_dump_json(indent=2, exclude_none=True))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def load_session(self, account_id: str | None = None) -> AccountSession | None:
        if account_id:
            path = self.account_session_path(account_id)
        else:
            path = self.session_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return AccountSession.model_validate_json(raw)

    def list_account_ids(self) -> list[str]:
        try:
            parsed = json.loads(self.account_index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, str) and item]

    def _add_account_id(self, account_id: str) -> None:
        ids = self.list_account_ids()
        if account_id in ids:
            return
        ids.append(account_id)
        _atomic_write_text(self.account_index_path, json.dumps(ids, ensure_ascii=False, indent=2))

    def save_cursor(self, cursor: str, account_id: str | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.cursor_path(account_id),
            json.dumps({"get_updates_buf": cursor}, ensure_ascii=False),
        )

    def load_cursor(self, account_id: str | None = None) -> str:
        try:
            raw = self.cursor_path(account_id).read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            return ""
        value = parsed.get("get_updates_buf")
        return value if isinstance(value, str) else ""

    def load_seen_message_ids(self, account_id: str | None = None) -> list[int]:
        try:
            parsed = json.loads(self.seen_path(account_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, int)]

    def save_seen_message_ids(
        self,
        message_ids: list[int],
        account_id: str | None = None,
        *,
        limit: int = 1000,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.seen_path(account_id),
            json.dumps(message_ids[-limit:], ensure_ascii=False),
        )

    def save_pause_until(self, pause_until_monotonic_seconds: float, account_id: str | None = None) -> None:
        """Persist pause duration as wall-clock epoch seconds.

        The client converts monotonic deadlines into wall time when saving.
        """
        import time

        remaining = max(0.0, pause_until_monotonic_seconds - time.monotonic())
        epoch_until = time.time() + remaining
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.pause_path(account_id),
            json.dumps({"pause_until": epoch_until}, ensure_ascii=False),
        )

    def load_pause_remaining(self, account_id: str | None = None) -> float:
        import time

        try:
            parsed = json.loads(self.pause_path(account_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return 0.0
        value = parsed.get("pause_until")
        if not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, float(value) - time.time())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
