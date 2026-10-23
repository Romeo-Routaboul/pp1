import json
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from python.DB import database_transaction, decode_json


router = APIRouter(prefix="/eventos", tags=["eventos"])


class EventNotFoundError(Exception):
    pass


class GuestNotFoundError(Exception):
    pass


class EventRecord(TypedDict):
    id: int
    name: str


class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("El nombre del evento no puede estar vacío.")
        return normalized_name


class GuestProfileUpdate(BaseModel):
    attendance: Literal["si", "no"] | None = None
    restrictions: list[str] | None = None
    other_restriction: str | None = Field(default=None, max_length=500)

    @field_validator("restrictions")
    @classmethod
    def normalize_restrictions(cls, restrictions: list[str] | None) -> list[str] | None:
        if restrictions is None:
            return None
        return list(dict.fromkeys(item.strip() for item in restrictions if item.strip()))

    @field_validator("other_restriction")
    @classmethod
    def normalize_other_restriction(cls, restriction: str | None) -> str | None:
        return restriction.strip() if restriction is not None else None


class MySQLEventRepository:
    def list_events(self) -> list[EventRecord]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id, name FROM events ORDER BY id")
            return cursor.fetchall()

    def create_event(self, name: str) -> EventRecord:
        with database_transaction() as (_, cursor):
            cursor.execute("INSERT INTO events (name) VALUES (%s)", (name,))
            return {"id": cursor.lastrowid, "name": name}

    def list_event_guests(self, event_id: int) -> list[dict[str, object]]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id AS user_id, u.username,
                       TRIM(CONCAT(COALESCE(u.first_name, ''), ' ', COALESCE(u.last_name, ''))) AS name,
                       u.role, p.attendance, p.restrictions, p.other_restriction
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s
                ORDER BY u.id
                """,
                (event_id,),
            )
            guests = cursor.fetchall()
            for guest in guests:
                guest["restrictions"] = decode_json(guest["restrictions"], [])
            return guests

    def get_guest_profile(self, event_id: int, user_id: int) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT attendance, restrictions, other_restriction
                FROM event_guest_profiles
                WHERE event_id = %s AND user_id = %s
                """,
                (event_id, user_id),
            )
            profile = cursor.fetchone()
            if profile is None:
                raise GuestNotFoundError
            profile["restrictions"] = decode_json(profile["restrictions"], [])
            return profile

    def update_guest_profile(
        self,
        event_id: int,
        user_id: int,
        updates: dict[str, object],
    ) -> dict[str, object]:
        columns = {
            "attendance": "attendance",
            "restrictions": "restrictions",
            "other_restriction": "other_restriction",
        }
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            assignments: list[str] = []
            parameters: list[object] = []
            for field, value in updates.items():
                column = columns.get(field)
                if column is None:
                    continue
                assignments.append(f"{column} = %s")
                parameters.append(json.dumps(value, ensure_ascii=False) if field == "restrictions" else value)
            if assignments:
                cursor.execute(
                    f"""
                    UPDATE event_guest_profiles
                    SET {", ".join(assignments)}
                    WHERE event_id = %s AND user_id = %s
                    """,
                    (*parameters, event_id, user_id),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "SELECT user_id FROM event_guest_profiles WHERE event_id = %s AND user_id = %s",
                        (event_id, user_id),
                    )
                    if cursor.fetchone() is None:
                        raise GuestNotFoundError
            cursor.execute(
                """
                SELECT attendance, restrictions, other_restriction
                FROM event_guest_profiles
                WHERE event_id = %s AND user_id = %s
                """,
                (event_id, user_id),
            )
            profile = cursor.fetchone()
            if profile is None:
                raise GuestNotFoundError
            profile["restrictions"] = decode_json(profile["restrictions"], [])
            return profile

    def delete_event_guest(self, event_id: int, user_id: int) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id AS user_id, u.username,
                       TRIM(CONCAT(COALESCE(u.first_name, ''), ' ', COALESCE(u.last_name, ''))) AS name,
                       u.role, p.attendance, p.restrictions, p.other_restriction
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id = %s
                """,
                (event_id, user_id),
            )
            guest = cursor.fetchone()
            if guest is None:
                raise GuestNotFoundError
            guest["restrictions"] = decode_json(guest["restrictions"], [])
            cursor.execute(
                "DELETE FROM users WHERE id = %s AND event_id = %s",
                (user_id, event_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "DELETE FROM event_guest_profiles WHERE event_id = %s AND user_id = %s",
                    (event_id, user_id),
                )
            cursor.execute(
                """
                DELETE m FROM minors AS m
                LEFT JOIN minor_adults AS ma ON ma.event_id = m.event_id AND ma.minor_id = m.id
                WHERE m.event_id = %s AND ma.minor_id IS NULL
                """,
                (event_id,),
            )
            return guest


event_repository = MySQLEventRepository()


@router.get("")
def list_events() -> list[EventRecord]:
    return event_repository.list_events()


@router.post("", status_code=201)
def create_event(event: EventCreate) -> EventRecord:
    return event_repository.create_event(event.name)


@router.get("/{event_id}/invitados")
def list_event_guests(event_id: int) -> list[dict[str, object]]:
    try:
        return event_repository.list_event_guests(event_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error


@router.get("/{event_id}/invitados/{user_id}/perfil")
def get_guest_profile(event_id: int, user_id: int) -> dict[str, object]:
    try:
        return event_repository.get_guest_profile(event_id, user_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error
    except GuestNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="El invitado no pertenece a este evento.",
        ) from error


@router.patch("/{event_id}/invitados/{user_id}/perfil")
def update_guest_profile(
    event_id: int,
    user_id: int,
    updates: GuestProfileUpdate,
) -> dict[str, object]:
    try:
        return event_repository.update_guest_profile(
            event_id,
            user_id,
            updates.model_dump(exclude_unset=True),
        )
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error
    except GuestNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="El invitado no pertenece a este evento.",
        ) from error


@router.delete("/{event_id}/invitados/{user_id}")
def delete_event_guest(event_id: int, user_id: int) -> dict[str, object]:
    try:
        guest = event_repository.delete_event_guest(event_id, user_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error
    except GuestNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="El invitado no pertenece a este evento o ya fue eliminado.",
        ) from error
    return {"message": "Invitado eliminado correctamente.", "guest": guest}