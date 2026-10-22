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
            adult = self._find_adult_guest(event, user_id)
            minors = adult.setdefault("menores", [])
            if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
                raise ValueError("La lista de menores del invitado es inválida.")
            return minors

    def create_minor(self, event_id: int, user_id: int, minor_data: MinorCreate) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = self._find_event(data, event_id)
            adult = self._find_adult_guest(event, user_id)
            minors = adult.setdefault("menores", [])
            if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
                raise ValueError("La lista de menores del invitado es inválida.")
            minor_id = max(
                (int(minor.get("id", 0)) for minor in minors),
                default=0,
            ) + 1
            minor: dict[str, object] = {
                "id": minor_id,
                "name": minor_data.name,
                "age": minor_data.age,
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
            adult = self._find_adult_guest(event, user_id)
            minors = adult.get("menores", [])
            if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
                raise ValueError("La lista de menores del invitado es inválida.")
            minor = next(
                (item for item in minors if item.get("id") == minor_id),
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
            adult = self._find_adult_guest(event, user_id)
            minors = adult.get("menores", [])
            if not isinstance(minors, list) or any(not isinstance(minor, dict) for minor in minors):
                raise ValueError("La lista de menores del invitado es inválida.")
            index = next(
                (index for index, minor in enumerate(minors) if minor.get("id") == minor_id),
                None,
            )
            if index is None:
                raise MinorNotFoundError
            removed = minors.pop(index)
            self._write_data(data)
            return removed


minor_repository = JsonMinorRepository(PLACEHOLDER_PATH)


def _handle_error(error: Exception) -> HTTPException:
    if isinstance(error, EventNotFoundError):
        return HTTPException(status_code=404, detail="El evento seleccionado no existe.")
    if isinstance(error, AdultGuestNotFoundError):
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


@router.post("/{event_id}/invitados/{user_id}/menores", status_code=201)
def create_minor(event_id: int, user_id: int, minor: MinorCreate) -> dict[str, object]:
    try:
        return minor_repository.create_minor(event_id, user_id, minor)
    except (EventNotFoundError, AdultGuestNotFoundError) as error:
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
