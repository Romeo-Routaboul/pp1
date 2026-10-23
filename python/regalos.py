from typing import TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from python.DB import database_transaction


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


class MySQLGiftRepository:
    def list_event_gifts(self, event_id: int) -> list[dict[str, object]]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT g.id, g.name, g.description, g.image_url, g.purchase_url,
                       u.id AS reserved_by_user_id,
                       TRIM(CONCAT(COALESCE(u.first_name, ''), ' ',
                                   COALESCE(u.last_name, ''))) AS reserved_by_name
                FROM gifts AS g
                LEFT JOIN users AS u ON u.id = g.reserved_by_user_id
                WHERE g.event_id = %s
                ORDER BY g.id
                """,
                (event_id,),
            )
            gifts = cursor.fetchall()
            for gift in gifts:
                reserved_by_user_id = gift.pop("reserved_by_user_id")
                reserved_by_name = gift.pop("reserved_by_name")
                gift["reserved_by"] = (
                    {"user_id": reserved_by_user_id, "name": reserved_by_name}
                    if reserved_by_user_id is not None
                    else None
                )
            return gifts

    def create_event_gift(self, event_id: int, gift: GiftRecord) -> GiftRecord:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                INSERT INTO gifts (event_id, name, description, image_url, purchase_url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    gift["name"],
                    gift["description"],
                    gift["image_url"],
                    gift["purchase_url"],
                ),
            )
            return {**gift, "id": cursor.lastrowid, "reserved_by": None}

    def reserve_event_gift(self, event_id: int, gift_id: int, user_id: int) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                """,
                (event_id, user_id),
            )
            if cursor.fetchone() is None:
                raise GuestNotFoundError
            cursor.execute(
                """
                SELECT g.id, g.name, g.description, g.image_url, g.purchase_url,
                       g.reserved_by_user_id,
                       TRIM(CONCAT(COALESCE(u.first_name, ''), ' ',
                                   COALESCE(u.last_name, ''))) AS reserved_by_name
                FROM gifts AS g
                LEFT JOIN users AS u ON u.id = g.reserved_by_user_id
                WHERE g.event_id = %s AND g.id = %s
                FOR UPDATE
                """,
                (event_id, gift_id),
            )
            gift = cursor.fetchone()
            if gift is None:
                raise GiftNotFoundError
            if gift["reserved_by_user_id"] not in (None, user_id):
                raise GiftAlreadyReservedError
            cursor.execute(
                "UPDATE gifts SET reserved_by_user_id = %s WHERE id = %s",
                (user_id, gift_id),
            )
            gift["reserved_by"] = {"user_id": user_id, "name": gift.pop("reserved_by_name")}
            gift.pop("reserved_by_user_id")
            return gift

    def cancel_event_gift_reservation(
        self,
        event_id: int,
        gift_id: int,
        user_id: int,
    ) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT u.id
                FROM event_guest_profiles AS p
                JOIN users AS u ON u.id = p.user_id
                WHERE p.event_id = %s AND p.user_id = %s AND u.role = 'invitado'
                """,
                (event_id, user_id),
            )
            if cursor.fetchone() is None:
                raise GuestNotFoundError
            cursor.execute(
                """
                SELECT id, name, description, image_url, purchase_url, reserved_by_user_id
                FROM gifts WHERE event_id = %s AND id = %s FOR UPDATE
                """,
                (event_id, gift_id),
            )
            gift = cursor.fetchone()
            if gift is None:
                raise GiftNotFoundError
            if gift["reserved_by_user_id"] != user_id:
                raise GiftReservationNotOwnedError
            cursor.execute(
                "UPDATE gifts SET reserved_by_user_id = NULL WHERE id = %s",
                (gift_id,),
            )
            gift["reserved_by"] = None
            gift.pop("reserved_by_user_id")
            return gift


gift_repository = MySQLGiftRepository()


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