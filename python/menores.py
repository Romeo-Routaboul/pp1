import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from python.placeholder_storage import PLACEHOLDER_LOCK


PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "placeholder.json"
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


class JsonMinorRepository:
    def __init__(self, path: Path):
        self.path = path

    def _read_data(self) -> dict[str, object]:
        if not self.path.exists():
            return {"events": []}
        with self.path.open("r", encoding="utf-8") as data_file:
            data = json.load(data_file)
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
            ) as data_file:
                temporary_path = data_file.name
                json.dump(data, data_file, ensure_ascii=False, indent=2)
                data_file.write("\n")
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _find_adult_guest(event: dict[str, object], user_id: int) -> dict[str, object]:
        guests = event.get("guests", [])
        if not isinstance(guests, list):
            raise ValueError("La lista de invitados del evento es inválida.")
        guest = next(
            (
                item for item in guests
                if isinstance(item, dict)
                and item.get("user_id") == user_id
                and item.get("role") == "invitado"
            ),
            None,
        )
        if guest is None:
            raise AdultGuestNotFoundError
        return guest

    @staticmethod
    def _event_minors(event: dict[str, object]) -> list[dict[str, object]]:
        guests = event.get("guests", [])
        if not isinstance(guests, list):
            raise ValueError("La lista de invitados del evento es inválida.")

        minors = event.setdefault("menores", [])
        if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
            raise ValueError("La lista de menores del evento es inválida.")

        for minor in minors:
            adult_ids = minor.get("adult_ids", [])
            if not isinstance(adult_ids, list) or any(not isinstance(item, int) for item in adult_ids):
                raise ValueError("La lista de adultos asociados al menor es inválida.")

        next_id = max((int(minor.get("id", 0)) for minor in minors), default=0) + 1
        for guest in guests:
            if not isinstance(guest, dict):
                raise ValueError("La lista de invitados del evento es inválida.")
            legacy_minors = guest.pop("menores", [])
            if not isinstance(legacy_minors, list) or any(
                not isinstance(minor, dict) for minor in legacy_minors
            ):
                raise ValueError("La lista de menores del invitado es inválida.")
            user_id = guest.get("user_id")
            if not isinstance(user_id, int):
                continue
            for legacy_minor in legacy_minors:
                migrated_minor = dict(legacy_minor)
                migrated_minor["id"] = next_id
                migrated_minor["adult_ids"] = [user_id]
                minors.append(migrated_minor)
                next_id += 1
        return minors

    @staticmethod
    def _find_event(data: dict[str, object], event_id: int) -> dict[str, object]:
        events = data["events"]
        event = next(
            (item for item in events if isinstance(item, dict) and item.get("id") == event_id),
            None,
        )
        if event is None:
            raise EventNotFoundError
        return event

    def list_minors(self, event_id: int, user_id: int) -> list[dict[str, object]]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            self._find_adult_guest(event, user_id)
            minors = [
                minor for minor in self._event_minors(event)
                if user_id in minor.get("adult_ids", [])
            ]
            self._write_data(data)
            return minors

    def create_minor(self, event_id: int, user_id: int, minor_data: MinorCreate) -> dict[str, object]:
        return self.create_minor_for_adults(
            event_id,
            [user_id],
            MinorAssignment(**minor_data.model_dump(), adult_ids=[user_id]),
        )

    def list_event_minors(self, event_id: int) -> list[dict[str, object]]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            minors = self._event_minors(event)
            self._write_data(data)
            return minors

    def create_minor_for_adults(
        self,
        event_id: int,
        adult_ids: list[int],
        minor_data: MinorAssignment,
    ) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            guests = event.get("guests", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            valid_adult_ids = {
                guest.get("user_id")
                for guest in guests
                if isinstance(guest, dict) and guest.get("role") == "invitado"
            }
            if any(adult_id not in valid_adult_ids for adult_id in adult_ids):
                raise InvalidMinorAdultAssignmentError

            minors = self._event_minors(event)
            minor: dict[str, object] = {
                "id": max((int(item.get("id", 0)) for item in minors), default=0) + 1,
                "name": minor_data.name,
                "age": minor_data.age,
                "adult_ids": adult_ids,
                "attendance": None,
                "restrictions": [],
                "other_restriction": "",
            }
            minors.append(minor)
            self._write_data(data)
            return minor

    def update_minor(
        self,
        event_id: int,
        user_id: int,
        minor_id: int,
        updates: dict[str, object],
    ) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            self._find_adult_guest(event, user_id)
            minor = next(
                (
                    item for item in self._event_minors(event)
                    if item.get("id") == minor_id
                    and user_id in item.get("adult_ids", [])
                ),
                None,
            )
            if minor is None:
                raise MinorNotFoundError

            for field, value in updates.items():
                if field == "restrictions" and isinstance(value, list):
                    if any(not isinstance(item, str) for item in value):
                        raise ValueError("Las restricciones del menor son inválidas.")
                    value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
                elif field == "other_restriction" and isinstance(value, str):
                    value = value.strip()
                minor[field] = value
            self._write_data(data)
            return minor

    def delete_minor(self, event_id: int, user_id: int, minor_id: int) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            self._find_adult_guest(event, user_id)
            minors = self._event_minors(event)
            minor = next(
                (
                    item for item in minors
                    if item.get("id") == minor_id and user_id in item.get("adult_ids", [])
                ),
                None,
            )
            if minor is None:
                raise MinorNotFoundError
            adult_ids = minor.get("adult_ids")
            if not isinstance(adult_ids, list):
                raise ValueError("La lista de adultos asociados al menor es inválida.")
            minor["adult_ids"] = [adult_id for adult_id in adult_ids if adult_id != user_id]
            if not minor["adult_ids"]:
                minors.remove(minor)
            self._write_data(data)
            return minor


minor_repository = JsonMinorRepository(PLACEHOLDER_PATH)


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
