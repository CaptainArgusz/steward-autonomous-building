"""Weekend cleaning surge coordination using the S01 replay and capacity planner (S05)."""

from __future__ import annotations

import pandas as pd

from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset
from steward.detectors.turnaround import _Replay

ISSUE = "occupancy_surge"
MIN_ARRIVALS = 8

CAPACITY_MESSAGES = {
    "en": "We are preparing for a busy arrival day and checking the cleaning schedule for your room. We cannot confirm an early ready time yet. We will update you as soon as a reliable time is available.",
    "de": "Wir bereiten uns auf einen stark belegten Anreisetag vor und prüfen den Reinigungsplan für Ihr Zimmer. Eine frühere Bereitstellung können wir noch nicht bestätigen. Sobald wir eine verlässliche Zeit haben, informieren wir Sie.",
    "it": "Stiamo preparando una giornata di arrivi molto intensa e verificando la pulizia della vostra camera. Non possiamo ancora confermare un orario anticipato. Vi aggiorneremo appena avremo un orario affidabile.",
    "fr": "Nous préparons une journée d'arrivées chargée et vérifions le planning de nettoyage de votre chambre. Nous ne pouvons pas encore confirmer une heure anticipée. Nous vous informerons dès qu'une heure fiable sera disponible.",
    "es": "Estamos preparando un día con muchas llegadas y revisando la limpieza de su habitación. Aún no podemos confirmar una hora de disponibilidad anticipada. Le informaremos en cuanto tengamos una hora fiable.",
    "nl": "We bereiden ons voor op een drukke aankomstdag en controleren het schoonmaakschema voor uw kamer. We kunnen nog geen eerdere gereedheid bevestigen. Zodra we een betrouwbare tijd hebben, laten we het u weten.",
}


def coordinated_replay(ds: Dataset) -> tuple[list[Decision], list[Decision]]:
    cached = ds.__dict__.get("_coordinated_turnaround")
    if cached is not None:
        return cached
    replay = _Replay(ds)
    decisions: list[Decision] = []
    planned: set = set()
    notified: set[str] = set()

    def check(state: _Replay, at: pd.Timestamp, day) -> None:
        if day.weekday() < 5 or at > state.at_local(day, state.checkin_hm):
            return
        arrivals = state.arrivals_by_day.get(day)
        if arrivals is None:
            return
        known = arrivals[arrivals["created_utc"] <= at]
        unique_rooms = known.groupby("room_id").filter(lambda g: len(g) == 1)
        if len(unique_rooms) < MIN_ARRIVALS:
            return
        crew, absent = state.cleaners_at(at, day)
        if not absent:
            return
        slots = state.ready_times(at, day, "edf")
        short = []
        for b in unique_rooms.itertuples():
            need, booking = state.need_for(b.room_id, day, at)
            if booking != b.booking_id or need is None:
                continue
            slot = slots.get(b.room_id)
            if slot is not None and (slot.end is None or slot.end > need):
                short.append((b.room_id, booking, slot))
        if not short:
            return
        weekend = day - pd.Timedelta(days=day.weekday() - 5)
        episode = f"{ISSUE}:BUILDING:{weekend:%Y%m%d}"
        if day not in planned:
            planned.add(day)
            affected = [room for room, _, _ in short]
            evidence = absent + [
                f"bookings.csv:{len(unique_rooms)} unique arrival rooms known by {utc_z(at)} for {day}",
                f"staff_roster.csv:{len(crew)} cleaner(s) available for the shift on {day}",
                f"room_turnaround:shared shift-fit queue finds {len(short)} arrival room(s) at risk",
            ]
            plan = make_event_id(ISSUE, "BUILDING", at, "schedule_cleaning")
            feasible = [
                f"{room} by {slot.resource_id} around {utc_z(slot.end)}"
                for room, _, slot in short
                if slot.end is not None
            ]
            decisions.append(
                Decision(
                    event_id=plan,
                    episode_id=episode,
                    timestamp_utc=at,
                    affected_assets=["BUILDING", *affected],
                    issue_type=ISSUE,
                    action="schedule_cleaning",
                    priority="high",
                    requires_human_approval=False,
                    reason=f"Busy weekend with a known cleaner absence: {len(short)} booked arrival room(s) are at risk. Prioritise the shared S01 cleaning queue by guest need. "
                    + ("Feasible but late: " + "; ".join(feasible[:5]) + ". " if feasible else "")
                    + "Rooms without a feasible slot need extra capacity; robots only pre-clean vacant rooms and cannot release them.",
                    evidence=evidence,
                    expected_outcome="A coordinated priority plan is proposed; no room or extra staff is assumed ready.",
                    verification_plan="Cleaning completes before each guest's need; re-plan when a new absence, checkout or request changes capacity.",
                    confidence=0.7,
                )
            )
            decisions.append(
                Decision(
                    event_id=make_event_id(ISSUE, "BUILDING", at, "request_human_approval"),
                    episode_id=episode,
                    timestamp_utc=at,
                    affected_assets=["BUILDING", *affected],
                    issue_type=ISSUE,
                    action="request_human_approval",
                    priority="high",
                    requires_human_approval=True,
                    reason=f"Ask the duty manager to arrange and confirm additional cleaning capacity for {len(short)} at-risk room(s). Do not count external help or the waiting-guest-only on-call cleaner until actually available.",
                    evidence=evidence,
                    dependencies=[plan],
                    expected_outcome="Capacity request pending human action; no additional cleaner is booked.",
                    verification_plan="A confirmed extra shift or service booking supplies a non-overlapping slot; otherwise keep room readiness uncertain and update guests.",
                    confidence=0.7,
                )
            )

        messages: dict[str, list[tuple[str, str, str | None]]] = {}
        for room, booking, _ in short:
            if booking in notified or state.episode(room, day) in state.episodes_with_notice:
                continue
            notified.add(booking)
            state.episodes_with_notice.add(state.episode(room, day))
            lang = state.language_for(booking, at)
            lang = lang if lang in CAPACITY_MESSAGES else "en"
            requests = ds.guest_requests
            linked = requests[
                (requests["booking_id"] == booking)
                & (requests["timestamp_utc"] <= at)
                & (requests["request_type"] == "early_checkin")
            ]
            request_id = str(linked.iloc[-1]["request_id"]) if not linked.empty else None
            messages.setdefault(lang, []).append((room, booking, request_id))
        for lang, group in messages.items():
            rooms = [room for room, _, _ in group]
            bookings = [booking for _, booking, _ in group]
            request_ids = [request_id for _, _, request_id in group if request_id]
            decisions.append(
                Decision(
                    event_id=make_event_id(ISSUE, rooms[0], at, "notify_guest"),
                    episode_id=episode,
                    timestamp_utc=at,
                    affected_assets=rooms,
                    issue_type=ISSUE,
                    action="notify_guest",
                    priority="medium",
                    requires_human_approval=False,
                    reason=f"Bookings {', '.join(bookings)} are at risk under the known weekend cleaning capacity. Propose a separate conditional update to each listed room without inventing a ready time.",
                    evidence=[
                        *(
                            f"bookings.csv:{booking}:arrival {room} on {day}"
                            for room, booking, _ in group
                        ),
                        *absent,
                    ],
                    guest_request_ids=request_ids or None,
                    proposed_message=CAPACITY_MESSAGES[lang],
                    message_language=lang,
                    expected_outcome="Proposed guest message only; delivery is not confirmed.",
                    verification_plan="Send a revised, specific room-ready estimate only after a feasible cleaning slot is confirmed; check the request deadline if linked.",
                    confidence=0.65,
                )
            )

    room_decisions = replay.run(weekend_callback=check)
    result = (room_decisions, sorted(decisions, key=lambda d: (d.timestamp_utc, d.event_id)))
    ds.__dict__["_coordinated_turnaround"] = result
    return result


def detect(ds: Dataset) -> list[Decision]:
    return coordinated_replay(ds)[1]
