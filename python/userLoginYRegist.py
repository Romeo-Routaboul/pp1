import hashlib
import hmac
import os
from typing import Protocol, TypedDict

from mysql.connector import IntegrityError

from python.DB import database_transaction
from python.eventos import EventNotFoundError


PBKDF2_ITERATIONS = 600_000
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"


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


class MySQLUserRepository:
    def find_by_username(self, username: str) -> UserRecord | None:
        with database_transaction() as (_, cursor):
            cursor.execute(
                """
                SELECT id, username, password_hash, role, event_id
                FROM users WHERE username = %s
                """,
                (username,),
            )
            return cursor.fetchone()

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        event_id: int | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserRecord:
        try:
            with database_transaction() as (_, cursor):
                if event_id is not None:
                    cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
                    if cursor.fetchone() is None:
                        raise EventNotFoundError
                cursor.execute(
                    """
                    INSERT INTO users
                        (username, password_hash, role, event_id, first_name, last_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (username, password_hash, role, event_id, first_name, last_name),
                )
                user: UserRecord = {
                    "id": cursor.lastrowid,
                    "username": username,
                    "password_hash": password_hash,
                    "role": role,
                    "event_id": event_id,
                }
                if event_id is not None:
                    cursor.execute(
                        """
                        INSERT INTO event_guest_profiles
                            (event_id, user_id, attendance, restrictions, other_restriction)
                        VALUES (%s, %s, NULL, JSON_ARRAY(), '')
                        """,
                        (event_id, user["id"]),
                    )
                return user
        except IntegrityError as error:
            if error.errno == 1062:
                raise DuplicateUsernameError from error
            raise


user_repository: UserRepository = MySQLUserRepository()


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