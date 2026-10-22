import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from python.placeholder_storage import PLACEHOLDER_LOCK


PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "placeholder.json"
router = APIRouter(prefix="/eventos", tags=["regalos"])


class EventNotFoundError(Exception):
    pass


class GuestNotFoundError(Exception):
    pass


class GiftNotFoundError(Exception):
    pass


class GiftAlreadyReservedError(Exception):
    pass


class GiftReservationNotOwnedError(Exception):
    pass


class GiftRecord(TypedDict):
    id: int
    name: str
    description: str
    image_url: str | None
    purchase_url: str
    reserved_by: dict[str, int | str] | None


class GiftReservation(BaseModel):
    user_id: int = Field(gt=0)


class GiftCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    image_url: AnyHttpUrl | None = None
    purchase_url: AnyHttpUrl

    @field_validator("name", "description")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("El nombre y la descripción no pueden estar vacíos.")
        return normalized_value


class JsonGiftRepository:
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

    def list_event_gifts(self, event_id: int) -> list[dict[str, object]]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError

            gifts = event.get("regalos", [])
            if not isinstance(gifts, list) or any(not isinstance(gift, dict) for gift in gifts):
                raise ValueError("La lista de regalos del evento es inválida.")
            if any(not isinstance(gift.get("id"), int) for gift in gifts):
                next_id = max(
                    (gift["id"] for gift in gifts if isinstance(gift.get("id"), int)),
                    default=0,
                ) + 1
                for gift in gifts:
                    if not isinstance(gift.get("id"), int):
                        gift["id"] = next_id
                        next_id += 1
                self._write_data(data)
            return gifts

    def create_event_gift(self, event_id: int, gift: GiftRecord) -> GiftRecord:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError

            gifts = event.setdefault("regalos", [])
            if not isinstance(gifts, list) or any(not isinstance(item, dict) for item in gifts):
                raise ValueError("La lista de regalos del evento es inválida.")
            gift["id"] = max(
                (item["id"] for item in gifts if isinstance(item.get("id"), int)),
                default=0,
            ) + 1
            gift["reserved_by"] = None
            gifts.append(gift)
            self._write_data(data)
            return gift

    def reserve_event_gift(self, event_id: int, gift_id: int, user_id: int) -> dict[str, object]:
        with PLACEHOLDER_LOCK:
            data = self._read_data()
            event = next(
                (item for item in data["events"] if item["id"] == event_id),
                None,
            )
            if event is None:
                raise EventNotFoundError

            guests = event.get("guests", [])
            gifts = event.get("regalos", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            if not isinstance(gifts, list) or any(not isinstance(gift, dict) for gift in gifts):
                raise ValueError("La lista de regalos del evento es inválida.")

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
                raise GuestNotFoundError

            gift = next(
                (
                    item for item in gifts
                    if isinstance(item, dict) and item.get("id") == gift_id
                ),
                None,
            )
            if gift is None:
                raise GiftNotFoundError

            reserved_by = gift.get("reserved_by")
            if isinstance(reserved_by, dict) and reserved_by.get("user_id") != user_id:
                raise GiftAlreadyReservedError

            gift["reserved_by"] = {
                "user_id": user_id,
                "name": str(guest.get("name") or guest.get("username") or "Invitado"),
            }
            self._write_data(data)
            return gift

    def cancel_event_gift_reservation(
        self,
        event_id: int,
        gift_id: int,
        user_id: int,
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
            gifts = event.get("regalos", [])
            if not isinstance(guests, list):
                raise ValueError("La lista de invitados del evento es inválida.")
            if not isinstance(gifts, list) or any(not isinstance(gift, dict) for gift in gifts):
                raise ValueError("La lista de regalos del evento es inválida.")

            guest_exists = any(
                isinstance(guest, dict)
                and guest.get("user_id") == user_id
                and guest.get("role") == "invitado"
                for guest in guests
            )
            if not guest_exists:
                raise GuestNotFoundError

            gift = next(
                (
                    item for item in gifts
                    if isinstance(item, dict) and item.get("id") == gift_id
                ),
                None,
            )
            if gift is None:
                raise GiftNotFoundError

            reserved_by = gift.get("reserved_by")
            if not isinstance(reserved_by, dict) or reserved_by.get("user_id") != user_id:
                raise GiftReservationNotOwnedError

            gift["reserved_by"] = None
            self._write_data(data)
            return gift


gift_repository = JsonGiftRepository(PLACEHOLDER_PATH)


@router.get("/{event_id}/regalos")
def list_event_gifts(event_id: int) -> list[dict[str, object]]:
    try:
        return gift_repository.list_event_gifts(event_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error


@router.post("/{event_id}/regalos", status_code=201)
def create_event_gift(event_id: int, gift: GiftCreate) -> GiftRecord:
    gift_record: GiftRecord = {
        "id": 0,
        "name": gift.name,
        "description": gift.description,
        "image_url": str(gift.image_url) if gift.image_url is not None else None,
        "purchase_url": str(gift.purchase_url),
        "reserved_by": None,
    }
    try:
        return gift_repository.create_event_gift(event_id, gift_record)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error


@router.post("/{event_id}/regalos/{gift_id}/reservas")
def reserve_event_gift(event_id: int, gift_id: int, reservation: GiftReservation) -> dict[str, object]:
    try:
        return gift_repository.reserve_event_gift(event_id, gift_id, reservation.user_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error
    except GuestNotFoundError as error:
        raise HTTPException(
            status_code=403,
            detail="Solo un invitado asociado a este evento puede reservar regalos.",
        ) from error
    except GiftNotFoundError as error:
        raise HTTPException(status_code=404, detail="El regalo seleccionado no existe.") from error
    except GiftAlreadyReservedError as error:
        raise HTTPException(
            status_code=409,
            detail="Este regalo ya fue reservado por otro invitado.",
        ) from error


@router.delete("/{event_id}/regalos/{gift_id}/reservas/{user_id}")
def cancel_event_gift_reservation(event_id: int, gift_id: int, user_id: int) -> dict[str, object]:
    try:
        return gift_repository.cancel_event_gift_reservation(event_id, gift_id, user_id)
    except EventNotFoundError as error:
        raise HTTPException(status_code=404, detail="El evento seleccionado no existe.") from error
    except GuestNotFoundError as error:
        raise HTTPException(
            status_code=403,
            detail="Solo un invitado asociado a este evento puede cancelar una reserva.",
        ) from error
    except GiftNotFoundError as error:
        raise HTTPException(status_code=404, detail="El regalo seleccionado no existe.") from error
    except GiftReservationNotOwnedError as error:
        raise HTTPException(
            status_code=403,
            detail="Solo quien reservó este regalo puede deseleccionarlo.",
        ) from error