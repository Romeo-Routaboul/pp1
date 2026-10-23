import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from python.DB import database_transaction, decode_json


router = APIRouter(prefix="/eventos", tags=["menores"])


class EventNotFoundError(Exception):
    pass


class AdultGuestNotFoundError(Exception):
    pass


class InvalidMinorAdultAssignmentError(Exception):
    pass


class MinorNotFoundError(Exception):
    pass


class MinorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=0, le=17)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("El nombre del menor no puede estar vacío.")
        return normalized_name


class MinorAssignment(MinorCreate):
    adult_ids: list[int] = Field(min_length=1)

    @field_validator("adult_ids")
    @classmethod
    def validate_adult_ids(cls, adult_ids: list[int]) -> list[int]:
        if any(adult_id < 1 for adult_id in adult_ids):
            raise ValueError("Los adultos seleccionados no son válidos.")
        if len(set(adult_ids)) != len(adult_ids):
            raise ValueError("No se puede seleccionar dos veces al mismo adulto.")
        return adult_ids


class MinorUpdate(BaseModel):
    attendance: Literal["si", "no"] | None = None
    restrictions: list[str] | None = None
    other_restriction: str | None = Field(default=None, max_length=500)


class MySQLMinorRepository:
    @staticmethod
    def _event_exists(cursor: object, event_id: int) -> bool:
        cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
        return cursor.fetchone() is not None

    def list_minors(self, event_id: int, user_id: int) -> list[dict[str, object]]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT m.id, m.name, m.age, m.attendance, m.restrictions,
                       m.other_restriction, ma.user_id AS adult_id
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                JOIN minor_adults AS ma ON ma.event_id = p.event_id AND ma.user_id = p.user_id
                JOIN minors AS m ON m.event_id = ma.event_id AND m.id = ma.minor_id
                WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                ORDER BY m.id
                """,
                (event_id, user_id),
            )
            rows = cursor.fetchall()
            if not rows:
                cursor.execute(
                    """
                    SELECT u.id FROM event_guest_profiles AS p
                    JOIN users AS u ON u.id = p.user_id
                    WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                    """,
                    (event_id, user_id),
                )
                if cursor.fetchone() is None:
                    raise AdultGuestNotFoundError
            return self._group_minors(rows)

    def create_minor(self, event_id: int, user_id: int, minor_data: MinorCreate) -> dict[str, object]:
        return self.create_minor_for_adults(
            event_id,
            [user_id],
            MinorAssignment(**minor_data.model_dump(), adult_ids=[user_id]),
        )

    def list_event_minors(self, event_id: int) -> list[dict[str, object]]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT m.id, m.name, m.age, m.attendance, m.restrictions,
                       m.other_restriction, ma.user_id AS adult_id
                FROM minors AS m
                LEFT JOIN minor_adults AS ma ON ma.event_id = m.event_id AND ma.minor_id = m.id
                WHERE m.event_id = %s
                ORDER BY m.id, ma.user_id
                """,
                (event_id,),
            )
            return self._group_minors(cursor.fetchall())

    @staticmethod
    def _group_minors(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped: dict[int, dict[str, object]] = {}
        for row in rows:
            minor_id = int(row["id"])
            minor = grouped.setdefault(
                minor_id,
                {
                    "id": minor_id,
                    "name": row["name"],
                    "age": row["age"],
                    "adult_ids": [],
                    "attendance": row["attendance"],
                    "restrictions": decode_json(row["restrictions"], []),
                    "other_restriction": row["other_restriction"],
                },
            )
            if row.get("adult_id") is not None:
                minor["adult_ids"].append(row["adult_id"])
        return list(grouped.values())

    def create_minor_for_adults(
        self,
        event_id: int,
        adult_ids: list[int],
        minor_data: MinorAssignment,
    ) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            placeholders = ", ".join("%s" for _ in adult_ids)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id IN ({placeholders})
                    AND u.role = 'invitado'
                """,
                (event_id, *adult_ids),
            )
            if cursor.fetchone()["total"] != len(adult_ids):
                raise InvalidMinorAdultAssignmentError
            cursor.execute(
                """
                INSERT INTO minors (event_id, name, age, attendance, restrictions, other_restriction)
                VALUES (%s, %s, %s, NULL, JSON_ARRAY(), '')
                """,
                (event_id, minor_data.name, minor_data.age),
            )
            minor_id = cursor.lastrowid
            cursor.executemany(
                "INSERT INTO minor_adults (event_id, minor_id, user_id) VALUES (%s, %s, %s)",
                [(event_id, minor_id, adult_id) for adult_id in adult_ids],
            )
            return {
                "id": minor_id,
                "name": minor_data.name,
                "age": minor_data.age,
                "adult_ids": adult_ids,
                "attendance": None,
                "restrictions": [],
                "other_restriction": "",
            }

    def update_minor(
        self,
        event_id: int,
        user_id: int,
        minor_id: int,
        updates: dict[str, object],
    ) -> dict[str, object]:
        columns = {"attendance": "attendance", "restrictions": "restrictions", "other_restriction": "other_restriction"}
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                """,
                (event_id, user_id),
            )
            if cursor.fetchone() is None:
                raise AdultGuestNotFoundError
            assignments: list[str] = []
            parameters: list[object] = []
            for field, value in updates.items():
                column = columns.get(field)
                if column is None:
                    continue
                if field == "restrictions" and isinstance(value, list):
                    if any(not isinstance(item, str) for item in value):
                        raise ValueError("Las restricciones del menor son inválidas.")
                    value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
                elif field == "other_restriction" and isinstance(value, str):
                    value = value.strip()
                assignments.append(f"{column} = %s")
                parameters.append(json.dumps(value, ensure_ascii=False) if field == "restrictions" else value)
            if assignments:
                cursor.execute(
                    f"""
                    UPDATE minors AS m
                    JOIN minor_adults AS ma
                      ON ma.event_id = m.event_id AND ma.minor_id = m.id
                    SET {", ".join(assignments)}
                    WHERE m.event_id = %s AND m.id = %s AND ma.user_id = %s
                    """,
                    (*parameters, event_id, minor_id, user_id),
                )
            cursor.execute(
                """
                SELECT m.id, m.name, m.age, m.attendance, m.restrictions,
                       m.other_restriction, ma.user_id AS adult_id
                FROM minors AS m
                JOIN minor_adults AS ma ON ma.event_id = m.event_id AND ma.minor_id = m.id
                WHERE m.event_id = %s AND m.id = %s
                ORDER BY ma.user_id
                """,
                (event_id, minor_id),
            )
            rows = cursor.fetchall()
            if not rows or user_id not in [row["adult_id"] for row in rows]:
                raise MinorNotFoundError
            return self._group_minors(rows)[0]

    def delete_minor(self, event_id: int, user_id: int, minor_id: int) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                """,
                (event_id, user_id),
            )
            if cursor.fetchone() is None:
                raise AdultGuestNotFoundError
            cursor.execute(
                """
                SELECT m.id, m.name, m.age, m.attendance, m.restrictions,
                       m.other_restriction, ma.user_id AS adult_id
                FROM minors AS m
                JOIN minor_adults AS ma ON ma.event_id = m.event_id AND ma.minor_id = m.id
                WHERE m.event_id = %s AND m.id = %s
                ORDER BY ma.user_id
                """,
                (event_id, minor_id),
            )
            rows = cursor.fetchall()
            if not rows or user_id not in [row["adult_id"] for row in rows]:
                raise MinorNotFoundError
            minor = self._group_minors(rows)[0]
            cursor.execute(
                "DELETE FROM minor_adults WHERE event_id = %s AND minor_id = %s AND user_id = %s",
                (event_id, minor_id, user_id),
            )
            cursor.execute(
                """
                DELETE m FROM minors AS m
                LEFT JOIN minor_adults AS ma ON ma.event_id = m.event_id AND ma.minor_id = m.id
                WHERE m.event_id = %s AND m.id = %s AND ma.minor_id IS NULL
                """,
                (event_id, minor_id),
            )
            minor["adult_ids"] = [adult_id for adult_id in minor["adult_ids"] if adult_id != user_id]
            return minor


minor_repository = MySQLMinorRepository()


def _handle_error(error: Exception) -> HTTPException:
    if isinstance(error, EventNotFoundError):
        return HTTPException(status_code=404, detail="El evento seleccionado no existe.")
    if isinstance(error, (AdultGuestNotFoundError, InvalidMinorAdultAssignmentError)):
        return HTTPException(
            status_code=403,
            detail="Solo un invitado asociado a este evento puede gestionar menores.",
        )
    if isinstance(error, MinorNotFoundError):
        return HTTPException(status_code=404, detail="El menor seleccionado no existe.")
    raise error


@router.get("/{event_id}/invitados/{user_id}/menores")
def list_minors(event_id: int, user_id: int) -> list[dict[str, object]]:
    try:
        return minor_repository.list_minors(event_id, user_id)
    except (EventNotFoundError, AdultGuestNotFoundError) as error:
        raise _handle_error(error) from error


@router.get("/{event_id}/menores")
def list_event_minors(event_id: int) -> list[dict[str, object]]:
    try:
        return minor_repository.list_event_minors(event_id)
    except EventNotFoundError as error:
        raise _handle_error(error) from error


@router.post("/{event_id}/menores", status_code=201)
def create_event_minor(event_id: int, minor: MinorAssignment) -> dict[str, object]:
    try:
        return minor_repository.create_minor_for_adults(event_id, minor.adult_ids, minor)
    except EventNotFoundError as error:
        raise _handle_error(error) from error
    except InvalidMinorAdultAssignmentError as error:
        raise HTTPException(
            status_code=422,
            detail="Todos los adultos seleccionados deben ser invitados del evento.",
        ) from error


@router.post("/{event_id}/invitados/{user_id}/menores", status_code=201)
def create_minor(event_id: int, user_id: int, minor: MinorCreate) -> dict[str, object]:
    try:
        return minor_repository.create_minor(event_id, user_id, minor)
    except (EventNotFoundError, AdultGuestNotFoundError, InvalidMinorAdultAssignmentError) as error:
        raise _handle_error(error) from error


@router.patch("/{event_id}/invitados/{user_id}/menores/{minor_id}")
def update_minor(
    event_id: int,
    user_id: int,
    minor_id: int,
    updates: MinorUpdate,
) -> dict[str, object]:
    try:
        return minor_repository.update_minor(
            event_id,
            user_id,
            minor_id,
            updates.model_dump(exclude_unset=True),
        )
    except (EventNotFoundError, AdultGuestNotFoundError, MinorNotFoundError) as error:
        raise _handle_error(error) from error


@router.delete("/{event_id}/invitados/{user_id}/menores/{minor_id}")
def delete_minor(event_id: int, user_id: int, minor_id: int) -> dict[str, object]:
    try:
        return minor_repository.delete_minor(event_id, user_id, minor_id)
    except (EventNotFoundError, AdultGuestNotFoundError, MinorNotFoundError) as error:
        raise _handle_error(error) from error
