(() => {
    const eventIdValue = new URLSearchParams(window.location.search).get("event_id");
    try {
        window.pp1User = JSON.parse(sessionStorage.getItem("pp1User") || "null");
    } catch {
        window.pp1User = null;
    }

    const parsedQueryEventId = Number(eventIdValue);
    const userEventId = Number(window.pp1User?.event_id);
    const hasQueryEvent = Boolean(
        eventIdValue &&
        Number.isSafeInteger(parsedQueryEventId) &&
        parsedQueryEventId > 0,
    );
    const hasUserEvent = Boolean(
        Number.isSafeInteger(userEventId) && userEventId > 0,
    );
    const parsedEventId = hasQueryEvent ? parsedQueryEventId : userEventId;
    const hasEvent = hasQueryEvent || (!eventIdValue && hasUserEvent);

    window.pp1EventId = hasEvent ? parsedEventId : null;

    for (const link of document.querySelectorAll("[data-event-link]")) {
        if (hasEvent) {
            const target = new URL(link.href, window.location.origin);
            target.searchParams.set("event_id", parsedEventId);
            link.href = target.toString();
        } else {
            link.href = "#";
            link.setAttribute("aria-disabled", "true");
            link.addEventListener("click", (event) => event.preventDefault());
        }
    }

    for (const status of document.querySelectorAll("[data-event-status]")) {
        status.textContent = hasEvent
            ? `Opciones asociadas al evento #${parsedEventId}.`
            : "Esta opción requiere una cuenta asociada a un evento.";
    }

    if (!hasEvent) {
        for (const control of document.querySelectorAll("[data-event-required] button, [data-event-required] input, [data-event-required] select, [data-event-required] textarea")) {
            control.disabled = true;
        }
        for (const form of document.querySelectorAll("form[data-event-required]")) {
            form.addEventListener("submit", (event) => event.preventDefault());
        }
    }
})();
