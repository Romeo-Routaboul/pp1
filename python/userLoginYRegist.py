import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, TypedDict

from python.eventos import EventNotFoundError
from python.placeholder_storage import PLACEHOLDER_LOCK


PBKDF2_ITERATIONS = 600_000
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "placeholder.json"


class UserRecord(TypedDict):
    id: int
    username: str
    password_hash: str
    role: str | None
    event_id: int | None


class UserRepository(Protocol):
    def find_by_username(self, username: str) -> UserRecord | None: ...

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        event_id: int | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserRecord: ...


class DuplicateUsernameError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class JsonUserRepository:
    def __init__(self, path: Path):
        self.path = path

    def _read_data(self) -> dict[str, object]:
        if not self.path.exists():
            return {"users": [], "events": []}

        with self.path.open("r", encoding="utf-8") as users_file:
            data = json.load(users_file)

        if isinstance(data, list):
            data = {"users": data, "events": []}
        if not isinstance(data, dict):
            raise ValueError("El archivo debe contener un objeto JSON.")

        users = data.get("users")
        if not isinstance(users, list):
            raise ValueError("El archivo de usuarios debe contener una lista.")

        for user in users:
            if (
                not isinstance(user, dict)
                or not isinstance(user.get("id"), int)
                or not isinstance(user.get("username"), str)
            ):
                raise ValueError("El archivo contiene un registro de usuario inválido.")

        events = data.get("events", [])
        if not isinstance(events, list):
            raise ValueError("El archivo de eventos debe contener una lista.")
        data["events"] = events
        return data

    def _write_data(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                delete=False,
            ) as users_file:
                temporary_path = users_file.name
                json.dump(data, users_file, ensure_ascii=False, indent=2)
                users_file.write("\n")
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _to_user_record(user: dict[str, object]) -> UserRecord | None:
        password_hash = user.get("password_hash")
        if not isinstance(password_hash, str):
            return None

        return {
            "id": user["id"],
            "username": user["username"],
            "password_hash": password_hash,
            "role": user.get("role") if isinstance(user.get("role"), str) else None,
            "event_id": user.get("event_id") if isinstance(user.get("event_id"), int) else None,
        }

    def find_by_username(self, username: str) -> UserRecord | None:
        with PLACEHOLDER_LOCK:
            for user in self._read_data()["users"]:
                if str(user["username"]).casefold() == username.casefold():
                    return self._to_user_record(user)
        return None

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        event_id: int | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserRecord:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            users = data["users"]
            if any(str(user["username"]).casefold() == username.casefold() for user in users):
                raise DuplicateUsernameError

            selected_event: dict[str, object] | None = None
            if event_id is not None:
                selected_event = next(
                    (
                        event
                        for event in data["events"]
                        if isinstance(event, dict) and event.get("id") == event_id
                    ),
                    None,
                )
                if selected_event is None:
                    raise EventNotFoundError

            next_id = max((int(user["id"]) for user in users), default=0) + 1
            user: UserRecord = {
                "id": next_id,
                "username": username,
                "password_hash": password_hash,
                "role": role,
                "event_id": event_id,
            }
            user_data: dict[str, object] = dict(user)
            if event_id is not None:
                user_data["event_id"] = event_id
                user_data["first_name"] = first_name
                user_data["last_name"] = last_name
            users.append(user_data)
            if selected_event is not None:
                guests = selected_event.setdefault("guests", [])
                if not isinstance(guests, list):
                    raise ValueError("La lista de invitados del evento es inválida.")
                guests.append(
                    {
                        "user_id": next_id,
                        "username": username,
                        "name": f"{first_name} {last_name}",
                        "role": role,
                    }
                )
            data["users"] = users
            self._write_data(data)
            return user


user_repository: UserRepository = JsonUserRepository(PLACEHOLDER_PATH)


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "$".join(
        (
            PASSWORD_HASH_ALGORITHM,
            str(PBKDF2_ITERATIONS),
            salt.hex(),
            password_hash.hex(),
        )
    )


def _password_matches(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_hash = stored_hash.split("$")
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_text)
        expected_bytes = bytes.fromhex(expected_hash)
    except (TypeError, ValueError):
        return False

    if algorithm != PASSWORD_HASH_ALGORITHM or iterations <= 0:
        return False

    actual_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_hash, expected_bytes)


def _public_user(user: UserRecord) -> dict[str, int | str | None]:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "event_id": user["event_id"],
    }


def register_user(
    username: str,
    password: str,
    role: str = "invitado",
    event_id: int | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, int | str | None]:
    normalized_username = username.strip()
    if not normalized_username:
        raise ValueError("El nombre de usuario no puede estar vacío.")

    if role not in {"invitado", "anfitrion"}:
        raise ValueError("El rol debe ser invitado o anfitrión.")
    if event_id is None:
        raise ValueError("Debes asociar la cuenta a un evento.")
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if not first_name or not last_name:
        raise ValueError("El nombre y el apellido son obligatorios para el invitado.")

    user = user_repository.create_user(
        normalized_username,
        _hash_password(password),
        role,
        event_id,
        first_name,
        last_name,
    )
    return _public_user(user)


def login_user(username: str, password: str) -> dict[str, int | str | None]:
    normalized_username = username.strip()
    user = user_repository.find_by_username(normalized_username)
    if user is None or not _password_matches(password, user["password_hash"]):
        raise InvalidCredentialsError

    return _public_user(user)