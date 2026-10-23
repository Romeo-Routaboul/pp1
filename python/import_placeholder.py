import json
from pathlib import Path
from typing import Any

from python.DB import database_transaction


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_PATH = PROJECT_ROOT / "placeholder.json"
TABLES = (
    "table_assignments",
    "event_tables",
    "gifts",
    "minor_adults",
    "minors",
    "event_guest_profiles",
    "users",
    "events",
)


def _load_source() -> dict[str, Any]:
    with PLACEHOLDER_PATH.open("r", encoding="utf-8") as source_file:
        data = json.load(source_file)
    if not isinstance(data, dict):
        raise ValueError("placeholder.json debe contener un objeto JSON.")
    if not isinstance(data.get("users"), list) or not isinstance(data.get("events"), list):
        raise ValueError("placeholder.json debe incluir listas válidas de usuarios y eventos.")
    return data


def import_placeholder() -> dict[str, int]:
    data = _load_source()
    users = data["users"]
    events = data["events"]
    users_by_id = {
        user["id"]: user
        for user in users
        if isinstance(user, dict) and isinstance(user.get("id"), int)
    }
    if len(users_by_id) != len(users):
        raise ValueError("Todos los usuarios deben tener un ID entero único.")

    counts = {table: 0 for table in TABLES}
    with database_transaction() as (_, cursor):
        for table in TABLES:
            cursor.execute(f"SELECT COUNT(*) AS total FROM {table}")
            if cursor.fetchone()["total"]:
                raise RuntimeError(
                    f"La tabla {table} ya contiene datos; no se importó nada. "
                    "Usá una base vacía para evitar duplicados."
                )

        for event in events:
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("id"), int)
                or not isinstance(event.get("name"), str)
            ):
                raise ValueError("placeholder.json contiene un evento inválido.")
            event_id = event["id"]
            cursor.execute(
                "INSERT INTO events (id, name) VALUES (%s, %s)",
                (event_id, event["name"]),
            )
            counts["events"] += 1

        for user in users:
            role = user.get("role")
            if role not in {"admin", "invitado", "anfitrion"}:
                raise ValueError(f"El usuario {user['id']} tiene un rol inválido.")
            event_id = user.get("event_id")
            if event_id is not None and not any(event.get("id") == event_id for event in events):
                raise ValueError(f"El evento del usuario {user['id']} no existe en los datos.")
            cursor.execute(
                """
                INSERT INTO users
                    (id, username, password_hash, role, event_id, first_name, last_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user["id"],
                    user["username"],
                    user["password_hash"],
                    role,
                    event_id,
                    user.get("first_name"),
                    user.get("last_name"),
                ),
            )
            counts["users"] += 1

        for event in events:
            event_id = event["id"]
            guests = event.get("guests", [])
            minors = event.get("menores", [])
            gifts = event.get("regalos", [])
            tables = event.get("mesas", [])
            for guest in guests:
                user_id = guest.get("user_id")
                user = users_by_id.get(user_id)
                if user is None or user.get("event_id") != event_id:
                    raise ValueError(
                        f"El invitado {user_id} del evento {event_id} no tiene un usuario asociado."
                    )
                restrictions = guest.get("restrictions", [])
                cursor.execute(
                    """
                    INSERT INTO event_guest_profiles
                        (event_id, user_id, attendance, restrictions, other_restriction)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        user_id,
                        guest.get("attendance"),
                        json.dumps(restrictions, ensure_ascii=False),
                        guest.get("other_restriction", ""),
                    ),
                )
                counts["event_guest_profiles"] += 1

            for minor in minors:
                cursor.execute(
                    """
                    INSERT INTO minors
                        (id, event_id, name, age, attendance, restrictions, other_restriction)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        minor["id"],
                        event_id,
                        minor["name"],
                        minor["age"],
                        minor.get("attendance"),
                        json.dumps(minor.get("restrictions", []), ensure_ascii=False),
                        minor.get("other_restriction", ""),
                    ),
                )
                counts["minors"] += 1
                adult_ids = minor.get("adult_ids", [])
                for adult_id in adult_ids:
                    adult = users_by_id.get(adult_id)
                    if adult is None or adult.get("event_id") != event_id or adult.get("role") != "invitado":
                        raise ValueError(
                            f"El adulto {adult_id} asociado al menor {minor['id']} no es invitado del evento."
                        )
                    cursor.execute(
                        """
                        INSERT INTO minor_adults (event_id, minor_id, user_id)
                        VALUES (%s, %s, %s)
                        """,
                        (event_id, minor["id"], adult_id),
                    )
                    counts["minor_adults"] += 1

            for gift in gifts:
                reserved_by = gift.get("reserved_by")
                reserved_by_id = reserved_by.get("user_id") if isinstance(reserved_by, dict) else None
                cursor.execute(
                    """
                    INSERT INTO gifts
                        (id, event_id, name, description, image_url, purchase_url, reserved_by_user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        gift["id"],
                        event_id,
                        gift["name"],
                        gift.get("description", ""),
                        gift.get("image_url"),
                        gift["purchase_url"],
                        reserved_by_id,
                    ),
                )
                counts["gifts"] += 1

            for table in tables:
                cursor.execute(
                    """
                    INSERT INTO event_tables (id, event_id, name, seat_count)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (table["id"], event_id, table["name"], table["seat_count"]),
                )
                counts["event_tables"] += 1
                for assignment in table.get("assignments", []):
                    minor_id = assignment.get("minor_id")
                    guest_id = assignment["guest_id"]
                    if minor_id is None:
                        cursor.execute(
                            """
                            INSERT INTO table_assignments
                                (event_id, table_id, seat_number, guest_id)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (event_id, table["id"], assignment["seat_number"], guest_id),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO table_assignments
                                (event_id, table_id, seat_number, minor_id, minor_adult_id)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (event_id, table["id"], assignment["seat_number"], minor_id, guest_id),
                        )
                    counts["table_assignments"] += 1

    return counts


if __name__ == "__main__":
    imported_counts = import_placeholder()
    print("Importación completada:")
    for table, count in imported_counts.items():
        print(f"- {table}: {count}")
