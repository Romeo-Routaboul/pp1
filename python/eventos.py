import json
import os
import tempfile
from pathlib import Path
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from python.placeholder_storage import PLACEHOLDER_LOCK


PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "placeholder.json"
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


class JsonEventRepository:
    def __init__(self, path: Path):
        self.path = path

    def _read_data(self) -> dict[str, object]:
        if not self.path.exists():
            return {"events": []}

        with self.path.open("r", encoding="utf-8") as events_file:
            data = json.load(events_file)
        if not isinstance(data, dict):
            raise ValueError("El archivo debe contener un objeto JSON.")

        events = data.get("events", [])
        if not isinstance(events, list) or any(
            not isinstance(event, dict)
            or not isinstance(event.get("id"), int)
            or not isinstance(event.get("name"), str)
            for event in events
        ):
            raise ValueError("El archivo contiene una lista de eventos inválida.")
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
            ) as events_file:
                temporary_path = events_file.name
                json.dump(data, events_file, ensure_ascii=False, indent=2)
                events_file.write("\n")
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def list_events(self) -> list[EventRecord]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            return [
                {"id": event["id"], "name": event["name"]}
                for event in data["events"]
            ]

    def create_event(self, name: str) -> EventRecord:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            events = data["events"]
            event: EventRecord = {
                "id": max((int(item["id"]) for item in events), default=0) + 1,
                "name": name,
            }
            events.append({**event, "guests": []})
            self._write_data(data)
            return event

    def list_event_guests(self, event_id: int) -> list[dict[str, object]]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError

            guests = event.get("guests", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            return guests

    def get_guest_profile(self, event_id: int, user_id: int) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError
            guests = event.get("guests", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            guest = next(
                (
                    item for item in guests
                    if isinstance(item, dict) and item.get("user_id") == user_id
                ),
                None,
            )
            if guest is None:
                raise GuestNotFoundError
            return {
                "attendance": guest.get("attendance"),
                "restrictions": guest.get("restrictions", []),
                "other_restriction": guest.get("other_restriction", ""),
            }

    def update_guest_profile(
        self,
        event_id: int,
        user_id: int,
        updates: dict[str, object],
    ) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError
            guests = event.get("guests", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            guest = next(
                (
                    item for item in guests
                    if isinstance(item, dict) and item.get("user_id") == user_id
                ),
                None,
            )
            if guest is None:
                raise GuestNotFoundError
            guest.update(updates)
            self._write_data(data)
            return {
                "attendance": guest.get("attendance"),
                "restrictions": guest.get("restrictions", []),
                "other_restriction": guest.get("other_restriction", ""),
            }

    def delete_event_guest(self, event_id: int, user_id: int) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError

            guests = event.get("guests", [])
            users = data.get("users", [])
            if not isinstance(guests, list) or not isinstance(users, list):
                raise ValueError("Los datos de invitados del evento son inválidos.")

            guest_index = next(
                (
                    index
                    for index, guest in enumerate(guests)
                    if isinstance(guest, dict) and guest.get("user_id") == user_id
                ),
                None,
            )
            if guest_index is None:
                raise GuestNotFoundError

            guest = guests.pop(guest_index)
            minors = event.get("menores", [])
            if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
                raise ValueError("La lista de menores del evento es inválida.")
            remaining_minors = []
            for minor in minors:
                adult_ids = minor.get("adult_ids", [])
                if not isinstance(adult_ids, list):
                    raise ValueError("La lista de adultos asociados al menor es inválida.")
                minor["adult_ids"] = [adult_id for adult_id in adult_ids if adult_id != user_id]
                if minor["adult_ids"]:
                    remaining_minors.append(minor)
            event["menores"] = remaining_minors

            gifts = event.get("regalos", [])
            if not isinstance(gifts, list):
                raise ValueError("La lista de regalos del evento es inválida.")
            for gift in gifts:
                if not isinstance(gift, dict):
                    raise ValueError("La lista de regalos del evento es inválida.")
                reserved_by = gift.get("reserved_by")
                if isinstance(reserved_by, dict) and reserved_by.get("user_id") == user_id:
                    gift["reserved_by"] = None
            data["users"] = [
                user
                for user in users
                if not (
                    isinstance(user, dict)
                    and user.get("id") == user_id
                    and user.get("event_id") == event_id
                )
            ]
            self._write_data(data)
            return guest


event_repository = JsonEventRepository(PLACEHOLDER_PATH)


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