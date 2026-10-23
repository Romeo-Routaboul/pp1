from typing import Annotated

from fastapi import APIRouter, HTTPException
from mysql.connector import IntegrityError
from pydantic import BaseModel, Field, field_validator

from python.DB import database_transaction


router = APIRouter(prefix="/eventos", tags=["mesas"])
SeatCount = Annotated[int, Field(ge=1, le=100)]


class EventNotFoundError(Exception):
    pass


class TableNotFoundError(Exception):
    pass


class InvalidSeatAssignmentError(Exception):
    pass


class TableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    seat_count: SeatCount

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("El nombre de la mesa no puede estar vacío.")
        return normalized_name


class SeatAssignment(BaseModel):
    seat_number: int = Field(ge=1, le=100)
    guest_id: int = Field(gt=0)
    minor_id: int | None = Field(default=None, gt=0)


class TableUpdate(TableCreate):
    assignments: list[SeatAssignment] = Field(default_factory=list, max_length=100)


class MySQLTableRepository:
    @staticmethod
    def _event_exists(cursor: object, event_id: int) -> bool:
        cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
        return cursor.fetchone() is not None

    def list_tables(self, event_id: int) -> list[dict[str, object]]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT id, name, seat_count FROM event_tables
                WHERE event_id = %s ORDER BY id
                """,
                (event_id,),
            )
            tables = cursor.fetchall()
            if not tables:
                return []
            table_ids = [table["id"] for table in tables]
            placeholders = ", ".join("%s" for _ in table_ids)
            cursor.execute(
                f"""
                SELECT table_id, seat_number,
                       COALESCE(guest_id, minor_adult_id) AS guest_id, minor_id
                FROM table_assignments
                WHERE event_id = %s AND table_id IN ({placeholders})
                ORDER BY table_id, seat_number
                """,
                (event_id, *table_ids),
            )
            assignments_by_table: dict[int, list[dict[str, int | None]]] = {
                table_id: [] for table_id in table_ids
            }
            for assignment in cursor.fetchall():
                assignments_by_table[assignment["table_id"]].append({
                    "seat_number": assignment["seat_number"],
                    "guest_id": assignment["guest_id"],
                    "minor_id": assignment["minor_id"],
                })
            for table in tables:
                table["assignments"] = assignments_by_table[table["id"]]
            return tables

    def create_table(self, event_id: int, table_data: TableCreate) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            if not self._event_exists(cursor, event_id):
                raise EventNotFoundError
            cursor.execute(
                "INSERT INTO event_tables (event_id, name, seat_count) VALUES (%s, %s, %s)",
                (event_id, table_data.name, table_data.seat_count),
            )
            return {
                "id": cursor.lastrowid,
                "name": table_data.name,
                "seat_count": table_data.seat_count,
                "assignments": [],
            }

    def update_table(
        self,
        event_id: int,
        table_id: int,
        table_data: TableUpdate,
    ) -> dict[str, object]:
        try:
            return self._update_table(event_id, table_id, table_data)
        except IntegrityError as error:
            if error.errno in {1062, 1452}:
                raise InvalidSeatAssignmentError from error
            raise

    def _update_table(
        self,
        event_id: int,
        table_id: int,
        table_data: TableUpdate,
    ) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s FOR UPDATE", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT id FROM event_tables WHERE event_id = %s AND id = %s FOR UPDATE
                """,
                (event_id, table_id),
            )
            if cursor.fetchone() is None:
                raise TableNotFoundError

            seats: set[int] = set()
            occupants: set[tuple[str, int]] = set()
            for assignment in table_data.assignments:
                if assignment.seat_number > table_data.seat_count or assignment.seat_number in seats:
                    raise InvalidSeatAssignmentError
                seats.add(assignment.seat_number)
                if assignment.minor_id is None:
                    cursor.execute(
                        """
                        SELECT u.role FROM event_guest_profiles AS p
                        JOIN users AS u ON u.id = p.user_id
                        WHERE p.event_id = %s AND p.user_id = %s
                            AND u.role IN ('invitado', 'anfitrion')
                        """,
                        (event_id, assignment.guest_id),
                    )
                    if cursor.fetchone() is None:
                        raise InvalidSeatAssignmentError
                    occupant = ("guest", assignment.guest_id)
                else:
                    cursor.execute(
                        """
                        SELECT ma.user_id FROM minor_adults AS ma
                        JOIN users AS u ON u.id = ma.user_id
                        WHERE ma.event_id = %s AND ma.minor_id = %s AND ma.user_id = %s
                            AND u.role = 'invitado'
                        """,
                        (event_id, assignment.minor_id, assignment.guest_id),
                    )
                    if cursor.fetchone() is None:
                        raise InvalidSeatAssignmentError
                    occupant = ("minor", assignment.minor_id)
                if occupant in occupants:
                    raise InvalidSeatAssignmentError
                occupants.add(occupant)

            cursor.execute(
                "UPDATE event_tables SET name = %s, seat_count = %s WHERE id = %s",
                (table_data.name, table_data.seat_count, table_id),
            )
            cursor.execute(
                "DELETE FROM table_assignments WHERE event_id = %s AND table_id = %s",
                (event_id, table_id),
            )
            for assignment in table_data.assignments:
                if assignment.minor_id is None:
                    cursor.execute(
                        """
                        INSERT INTO table_assignments
                            (event_id, table_id, seat_number, guest_id)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (event_id, table_id, assignment.seat_number, assignment.guest_id),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO table_assignments
                            (event_id, table_id, seat_number, minor_id, minor_adult_id)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            event_id,
                            table_id,
                            assignment.seat_number,
                            assignment.minor_id,
                            assignment.guest_id,
                        ),
                    )
            return {
                "id": table_id,
                "name": table_data.name,
                "seat_count": table_data.seat_count,
                "assignments": [
                    {
                        "seat_number": item.seat_number,
                        "guest_id": item.guest_id,
                        "minor_id": item.minor_id,
                    }
                    for item in table_data.assignments
                ],
            }

    def delete_table(self, event_id: int, table_id: int) -> dict[str, object]:
        with database_transaction() as (_, cursor):
            cursor.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cursor.fetchone() is None:
                raise EventNotFoundError
            cursor.execute(
                """
                SELECT id, name, seat_count FROM event_tables
                WHERE event_id = %s AND id = %s
                """,
                (event_id, table_id),
            )
            table = cursor.fetchone()
            if table is None:
                raise TableNotFoundError
            cursor.execute(
                "DELETE FROM event_tables WHERE event_id = %s AND id = %s",
                (event_id, table_id),
            )
            return {**table, "assignments": []}


table_repository = MySQLTableRepository()


def _handle_error(error: Exception) -> HTTPException:
    if isinstance(error, EventNotFoundError):
        return HTTPException(status_code=404, detail="El evento seleccionado no existe.")
    if isinstance(error, TableNotFoundError):
        return HTTPException(status_code=404, detail="La mesa seleccionada no existe.")
    if isinstance(error, InvalidSeatAssignmentError):
        return HTTPException(
            status_code=422,
            detail="Los asientos deben ser únicos y solo pueden asignarse a invitados o menores asociados.",
        )
    raise error


@router.get("/{event_id}/mesas")
def list_tables(event_id: int) -> list[dict[str, object]]:
    try:
        return table_repository.list_tables(event_id)
    except EventNotFoundError as error:
        raise _handle_error(error) from error


@router.post("/{event_id}/mesas", status_code=201)
def create_table(event_id: int, table: TableCreate) -> dict[str, object]:
    try:
        return table_repository.create_table(event_id, table)
    except EventNotFoundError as error:
        raise _handle_error(error) from error


@router.put("/{event_id}/mesas/{table_id}")
def update_table(event_id: int, table_id: int, table: TableUpdate) -> dict[str, object]:
    try:
        return table_repository.update_table(event_id, table_id, table)
    except (EventNotFoundError, TableNotFoundError, InvalidSeatAssignmentError) as error:
        raise _handle_error(error) from error


@router.delete("/{event_id}/mesas/{table_id}")
def delete_table(event_id: int, table_id: int) -> dict[str, object]:
    try:
        deleted_table = table_repository.delete_table(event_id, table_id)
    except (EventNotFoundError, TableNotFoundError) as error:
        raise _handle_error(error) from error
    return {"message": "Mesa eliminada correctamente.", "table": deleted_table}
