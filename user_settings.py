"""Per-user bankroll/Kelly preferences with atomic local JSON persistence."""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
import os
from pathlib import Path
import tempfile

KELLY_MULTIPLIERS = {"FK": Fraction(1), "HK": Fraction(1, 2), "QK": Fraction(1, 4), "EK": Fraction(1, 8)}


def bankroll_value(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Bankroll must be a nonnegative finite amount")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("Bankroll must be a nonnegative finite amount")
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError("Bankroll is outside the supported range") from error


@dataclass(frozen=True)
class UserSettings:
    bankroll: Decimal | None = None
    bankroll_enabled: bool = True
    kelly: str = "QK"

    @property
    def multiplier(self) -> Fraction:
        return KELLY_MULTIPLIERS[self.kelly]

    @property
    def visible_bankroll(self) -> Decimal | None:
        return self.bankroll if self.bankroll_enabled else None


class SettingsStore:
    def __init__(self, path: str | Path = "user_data.json"):
        self.path = Path(path)
        self._users: dict[str, UserSettings] = {}
        if self.path.exists():
            with self.path.open() as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                raise ValueError("Settings file must contain a JSON object")
            # Preserve valid bankroll/Kelly settings, discard obsolete devig keys.
            # Leave the on-disk file untouched until a successful settings update.
            for user_id, record in raw.items():
                if not isinstance(record, dict):
                    raise ValueError("Invalid user settings record")
                bankroll = record.get("bankroll")
                kelly = record.get("kelly", "QK")
                if not isinstance(kelly, str) or kelly not in KELLY_MULTIPLIERS:
                    kelly = "QK"
                enabled = record.get("bankroll_enabled", True)
                self._users[user_id] = UserSettings(
                    bankroll_value(bankroll) if bankroll is not None else None,
                    enabled if isinstance(enabled, bool) else True,
                    kelly,
                )

    def get(self, user_id: str) -> UserSettings:
        return self._users.get(str(user_id), UserSettings())

    def update(self, user_id: str, *, bankroll: float | None = None,
               bankroll_enabled: bool | None = None, kelly: str | None = None) -> UserSettings:
        current = self.get(user_id)
        if bankroll is not None:
            current = replace(current, bankroll=bankroll_value(bankroll))
        if bankroll_enabled is not None:
            if not isinstance(bankroll_enabled, bool):
                raise ValueError("Bankroll visibility must be true or false")
            current = replace(current, bankroll_enabled=bankroll_enabled)
        if kelly is not None:
            if kelly not in KELLY_MULTIPLIERS:
                raise ValueError("Kelly must be FK, HK, QK, or EK")
            current = replace(current, kelly=kelly)
        updated = {**self._users, str(user_id): current}
        serialized = {
            key: {"bankroll": str(value.bankroll) if value.bankroll is not None else None,
                  "bankroll_enabled": value.bankroll_enabled, "kelly": value.kelly}
            for key, value in updated.items()
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".settings-", delete=False) as stream:
                temporary = stream.name
                json.dump(serialized, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        self._users = updated
        return current
