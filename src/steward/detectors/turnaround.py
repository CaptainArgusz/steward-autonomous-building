"""S01 The Perfect Checkout - turnaround conflicts (Level 1).

Method: a causal replay of the house with a small cleaning-queue simulation.

State per room (occupied / dirty since / cleaning in progress / robot done) is rebuilt from
events as they happen. At each decision moment the day's turnovers are scheduled onto the
cleaners believed to be on shift *at that moment* (a sick shift only counts once
``status_known_utc`` has passed), twice: FIFO (what happens if nobody intervenes) and
earliest-deadline-first (our plan). A room is in conflict when its guest needs it before the
simulated ready time.

Decision moments:
  * an early_checkin request arrives               -> can the stated time be guaranteed?
  * the last moment cleaning could still make it   -> re-check with the real house state
  * 07:00 local, shift start                       -> day-level capacity check for 15:00 arrivals
  * a late_checkout request arrives                -> would granting it delay the next guest?
  * a guest is recorded waiting / asks for an update -> realistic time, on-call if rules allow

Where the numbers come from: check-in/out times, shift and on-call rules from building.json;
cleaning and robot durations and the typical checkout time are trailing statistics of the
same dataset (defaults only during warm-up). Nothing is fitted to a date or a room.

Routine turnovers produce no decisions. One ``episode_id`` per room-day (or per day for a
capacity shortfall). ``release_room`` is never emitted: only a cleaner's cleaning_completed
releases a room.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from steward import audit
from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "room_turnaround"

# Warm-up defaults, replaced by trailing medians once MIN_SAMPLES observations exist.
DEFAULT_CLEAN_MIN = 60.0  # human cleaning without robot pre-clean
DEFAULT_CLEAN_ROBOT_MIN = 45.0  # human cleaning after a robot floor clean
DEFAULT_ROBOT_MIN = 30.0  # robot floor clean
MIN_SAMPLES = 20
TRAILING = 300

TOLERANCE = pd.Timedelta(minutes=15)  # one telemetry step: below this a "delay" is noise
LONG_WAIT = pd.Timedelta(hours=2)  # beyond this, offer another room instead of a wait
ONCALL_TRAVEL = pd.Timedelta(minutes=45)  # building.json staff notes
PLAN_HOUR_LOCAL = 7  # housekeeping shift start: the roster for the day is known

_TIME_RE = re.compile(r"\b(\d{1,2})[:.h](\d{2})\b")

_MESSAGES = {
    "eta": {
        "en": "Thank you for letting us know. The room is still being prepared after the previous guest; we expect it to be ready at about {eta}. We will prioritise it and let you know if it is ready earlier.",
        "de": "Danke für Ihre Nachricht. Das Zimmer wird nach der Abreise des vorherigen Gastes noch vorbereitet; wir rechnen damit, dass es gegen {eta} Uhr bereit ist. Wir reinigen es bevorzugt und melden uns, falls es früher fertig ist.",
        "it": "Grazie per l'avviso. La camera è ancora in preparazione dopo la partenza dell'ospite precedente; prevediamo che sia pronta verso le {eta}. Le daremo priorità e vi avviseremo se sarà pronta prima.",
        "fr": "Merci de nous avoir prévenus. La chambre est encore en préparation après le départ du client précédent ; nous prévoyons qu'elle soit prête vers {eta}. Nous la traitons en priorité et vous préviendrons si elle est prête plus tôt.",
        "es": "Gracias por avisarnos. La habitación aún se está preparando tras la salida del huésped anterior; calculamos que estará lista hacia las {eta}. Le damos prioridad y le avisaremos si está lista antes.",
        "nl": "Bedankt voor uw bericht. De kamer wordt nog klaargemaakt na het vertrek van de vorige gast; we verwachten dat deze rond {eta} klaar is. We geven de kamer voorrang en laten het weten als het eerder lukt.",
    },
    "waiting": {
        "en": "We are sorry for the wait. Your room is being cleaned and we expect it to be ready at about {eta}. You are welcome to leave your luggage in the lobby.",
        "de": "Wir entschuldigen uns für die Wartezeit. Ihr Zimmer wird gerade gereinigt und ist voraussichtlich gegen {eta} Uhr bereit. Ihr Gepäck können Sie gern in der Lobby abstellen.",
        "it": "Ci scusiamo per l'attesa. La camera è in fase di pulizia e prevediamo che sia pronta verso le {eta}. Potete lasciare i bagagli nella lobby.",
        "fr": "Nous sommes désolés pour l'attente. Votre chambre est en cours de nettoyage et devrait être prête vers {eta}. Vous pouvez laisser vos bagages dans le hall.",
        "es": "Lamentamos la espera. Su habitación se está limpiando y calculamos que estará lista hacia las {eta}. Puede dejar su equipaje en el vestíbulo.",
        "nl": "Excuses voor het wachten. Uw kamer wordt schoongemaakt en is naar verwachting rond {eta} klaar. U kunt uw bagage in de lobby laten staan.",
    },
    "late_checkout": {
        "en": "Unfortunately we cannot offer a check-out at {asked}: the next guest arrives today and the room has to be cleaned first. The latest we can offer is {offer}.",
        "de": "Leider können wir einen Check-out um {asked} Uhr nicht anbieten: Der nächste Gast reist heute an und das Zimmer muss vorher gereinigt werden. Spätestens möglich ist {offer} Uhr.",
        "it": "Purtroppo non possiamo offrire il check-out alle {asked}: l'ospite successivo arriva oggi e la camera deve essere pulita prima. Possiamo offrire al più tardi le {offer}.",
        "fr": "Malheureusement nous ne pouvons pas proposer un départ à {asked} : le client suivant arrive aujourd'hui et la chambre doit être nettoyée avant. Au plus tard, nous pouvons proposer {offer}.",
        "es": "Lamentablemente no podemos ofrecer la salida a las {asked}: el siguiente huésped llega hoy y la habitación debe limpiarse antes. Lo más tarde que podemos ofrecer es a las {offer}.",
        "nl": "Helaas kunnen we een check-out om {asked} niet aanbieden: de volgende gast komt vandaag aan en de kamer moet eerst worden schoongemaakt. Uiterlijk {offer} is mogelijk.",
    },
}


def parse_requested_time(text: str) -> tuple[int, int] | None:
    """Clock time (hour, minute) a guest names in a request, or None."""
    m = _TIME_RE.search(str(text))
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    return (hour, minute) if hour < 24 and minute < 60 else None


def render_message(kind: str, language: str, **params: str) -> tuple[str, str]:
    """(text, language) - falls back to English for a language we have no template for."""
    lang = language if language in _MESSAGES[kind] else "en"
    return _MESSAGES[kind][lang].format(**params), lang


# ------------------------------------------------------------------ queue simulation


@dataclass
class Task:
    room: str
    available: pd.Timestamp  # when a cleaner can start (room vacant)
    need: pd.Timestamp | None  # when the next guest needs the room (None = no arrival today)
    robot_ready: pd.Timestamp | None  # when a robot floor clean would be finished


@dataclass
class Cleaner:
    resource_id: str
    free_at: pd.Timestamp
    shift_end: pd.Timestamp


@dataclass
class Slot:
    start: pd.Timestamp | None
    end: pd.Timestamp | None  # None = no capacity left in today's shifts
    resource_id: str | None


def plan_queue(
    tasks: list[Task],
    cleaners: list[Cleaner],
    clean_min: float,
    clean_robot_min: float,
    policy: str = "edf",
) -> dict[str, Slot]:
    """Greedy list scheduling of cleaning tasks onto cleaners.

    ``edf`` serves the earliest guest need first, ``fifo`` the room vacated first. A cleaner
    must finish inside the shift; whatever is left over gets ``end=None``.
    """
    far = pd.Timestamp.max.tz_localize("UTC")
    pending = list(tasks)
    crew = [Cleaner(c.resource_id, c.free_at, c.shift_end) for c in cleaners]
    out: dict[str, Slot] = {}
    while pending and crew:
        crew.sort(key=lambda c: (c.free_at, c.resource_id))
        c = crew[0]
        ready = [t for t in pending if t.available <= c.free_at]
        if not ready:
            c.free_at = min(t.available for t in pending)
            ready = [t for t in pending if t.available <= c.free_at]
        key = (
            (lambda task: (task.need or far, task.available, task.room))
            if policy == "edf"
            else (lambda task: (task.available, task.room))
        )
        task = None
        end = None
        for candidate in sorted(ready, key=key):
            robot = candidate.robot_ready is not None and candidate.robot_ready <= c.free_at
            candidate_end = c.free_at + pd.Timedelta(
                minutes=clean_robot_min if robot else clean_min
            )
            if candidate_end <= c.shift_end:
                task, end = candidate, candidate_end
                break
        if task is None:
            crew.pop(0)
            continue
        start = c.free_at
        out[task.room] = Slot(start, end, c.resource_id)
        c.free_at = end
        pending.remove(task)
    for t in pending:
        out[t.room] = Slot(None, None, None)
    return out


# ------------------------------------------------------------------------ house state


@dataclass
class Room:
    occupied: bool = False
    booking: str | None = None
    dirty_since: pd.Timestamp | None = None
    checkout_event: str | None = None
    cleaning_by: str | None = None
    cleaning_started: pd.Timestamp | None = None
    robot_started: pd.Timestamp | None = None
    robot_done: pd.Timestamp | None = None
    robot_by: str | None = None


@dataclass
class Watch:
    """An early check-in request whose feasibility depends on how the morning goes."""

    request_id: str
    booking: str
    room: str
    need: pd.Timestamp
    language: str
    asked_at: pd.Timestamp
    done: bool = False


@dataclass
class _Stats:
    clean: deque = field(default_factory=lambda: deque(maxlen=TRAILING))
    clean_robot: deque = field(default_factory=lambda: deque(maxlen=TRAILING))
    robot: deque = field(default_factory=lambda: deque(maxlen=TRAILING))
    checkout_minute: deque = field(default_factory=lambda: deque(maxlen=TRAILING))

    @staticmethod
    def _median(values: deque, default: float) -> float:
        return float(np.median(values)) if len(values) >= MIN_SAMPLES else default

    @property
    def clean_min(self) -> float:
        return self._median(self.clean, DEFAULT_CLEAN_MIN)

    @property
    def clean_robot_min(self) -> float:
        return self._median(self.clean_robot, DEFAULT_CLEAN_ROBOT_MIN)

    @property
    def robot_min(self) -> float:
        return self._median(self.robot, DEFAULT_ROBOT_MIN)


def _hhmm(ts: pd.Timestamp, tz) -> str:
    return ts.tz_convert(tz).strftime("%H:%M")


def _ceil_quarter(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.ceil("15min")


class _Replay:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.tz = ds.local_tz
        ops = ds.building["operations"]
        self.checkin_hm = tuple(int(x) for x in ops["check_in_from_local"].split(":"))
        self.checkout_hm = tuple(int(x) for x in ops["check_out_until_local"].split(":"))
        self.oncall_start_hm = (15, 30)
        note = str(ds.building["staff"]["notes"].get("on_call_housekeeping", ""))
        m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", note)
        self.oncall_end_hm = (22, 0)
        if m:
            self.oncall_start_hm = (int(m.group(1)), int(m.group(2)))
            self.oncall_end_hm = (int(m.group(3)), int(m.group(4)))

        self.rooms: dict[str, Room] = {r: Room() for r in ds.room_ids}
        self.stats = _Stats()
        self.decisions: list[Decision] = []
        self.episodes_with_notice: set[str] = set()
        self.watches: list[Watch] = []
        self.day_flagged: set = set()
        self.waiting: dict[tuple, pd.Timestamp] = {}  # (room, day) -> guest at the door since
        self.spare_offered: set[tuple] = set()  # (room, day) already proposed for a move
        self.robot_reserved_until: dict[str, pd.Timestamp] = {}
        self.oncall_reserved_until: dict = {}
        self.oncall_eta_by_episode: dict[str, pd.Timestamp] = {}

        b = ds.bookings_expected.copy()  # no hindsight about cancellations
        b["arr_day"] = b["arrival_utc"].dt.tz_convert(self.tz).dt.date
        b["dep_day"] = b["departure_utc"].dt.tz_convert(self.tz).dt.date
        self.bookings = b.set_index("booking_id")
        self.arrivals_by_day = {d: g for d, g in b.groupby("arr_day")}
        roster = ds.staff_roster.copy()
        roster["day"] = roster["start_utc"].dt.tz_convert(self.tz).dt.date
        self.roster_by_day = {d: g for d, g in roster.groupby("day")}
        req = ds.guest_requests
        self.early_requests = req[req["request_type"] == "early_checkin"]
        self.requests_by_booking = {
            booking: rows.sort_values("timestamp_utc")
            for booking, rows in req.groupby("booking_id")
        }

    def language_for(self, booking: str | None, at: pd.Timestamp) -> str:
        """Guest language from requests already received, with an English fallback."""
        rows = self.requests_by_booking.get(booking)
        if rows is not None:
            known = rows[(rows["timestamp_utc"] <= at) & rows["language"].notna()]
            if not known.empty:
                return str(known.iloc[-1]["language"])
        return "en"

    # ------------------------------------------------------------- time helpers

    def at_local(self, day, hm: tuple[int, int]) -> pd.Timestamp:
        local = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hm[0], minute=hm[1])
        return local.tz_localize(self.tz).tz_convert("UTC")

    def day_of(self, ts: pd.Timestamp):
        return ts.tz_convert(self.tz).date()

    # ---------------------------------------------------------------- knowledge

    def cleaners_at(self, t: pd.Timestamp, day) -> tuple[list[Cleaner], list[str]]:
        """Cleaners believed to be working on ``day`` given what is known at ``t``."""
        rows = self.roster_by_day.get(day)
        crew: list[Cleaner] = []
        absent: list[str] = []
        if rows is None:
            return crew, absent
        for r in rows[rows["role"] == "cleaner"].itertuples():
            if r.status != "scheduled" and r.status_known_utc <= t:
                absent.append(f"staff_roster.csv:{r.shift_id}:{r.resource_id} {r.status}")
                continue
            if r.end_utc <= t:
                continue
            free = max(r.start_utc, t)
            for room in self.rooms.values():
                if room.cleaning_by == r.resource_id and room.cleaning_started is not None:
                    free = max(
                        free, room.cleaning_started + pd.Timedelta(minutes=self.stats.clean_min)
                    )
            crew.append(Cleaner(r.resource_id, free, r.end_utc))
        return crew, absent

    def robot_on_shift(self, t: pd.Timestamp) -> str | None:
        rows = self.roster_by_day.get(self.day_of(t))
        if rows is None:
            return None
        for r in rows[rows["role"] == "robot"].itertuples():
            known_absent = r.status != "scheduled" and r.status_known_utc <= t
            if known_absent or not (r.start_utc <= t < r.end_utc):
                continue
            if t + pd.Timedelta(minutes=self.stats.robot_min) > r.end_utc:
                continue
            if self.robot_reserved_until.get(r.resource_id, t) > t:
                continue
            if any(
                room.robot_by == r.resource_id
                and room.robot_started is not None
                and room.robot_done is None
                for room in self.rooms.values()
            ):
                continue
            return r.resource_id
        return None

    def reserve_robot(self, resource_id: str, at: pd.Timestamp) -> None:
        self.robot_reserved_until[resource_id] = at + pd.Timedelta(minutes=self.stats.robot_min)

    def oncall_slot(self, day, at: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        start = max(
            at + ONCALL_TRAVEL,
            self.at_local(day, self.oncall_start_hm) + ONCALL_TRAVEL,
            self.oncall_reserved_until.get(day, at),
        )
        end = start + pd.Timedelta(minutes=self.stats.clean_min)
        if end > self.at_local(day, self.oncall_end_hm):
            return None
        return start, end

    def need_for(self, room: str, day, t: pd.Timestamp) -> tuple[pd.Timestamp | None, str | None]:
        """(when the next guest needs ``room`` on ``day``, booking id), from what is known at t."""
        arr = self.known_arrivals(room, day, t)
        if len(arr) != 1:
            return None, None  # cancellation timing unknown: no automatic promise
        booking = arr.iloc[0]["booking_id"]
        need = self.at_local(day, self.checkin_hm)
        for w in self.watches:
            if w.booking == booking and w.asked_at <= t:
                need = min(need, w.need)
        if (room, day) in self.waiting:  # a guest already at the door comes first
            need = min(need, self.waiting[(room, day)])
        return need, booking

    def known_arrivals(self, room: str, day, t: pd.Timestamp) -> pd.DataFrame:
        """Reservations for this room and arrival day visible by ``t``."""
        arr = self.arrivals_by_day.get(day)
        if arr is None:
            return self.ds.bookings.iloc[:0]
        return arr[(arr["room_id"] == room) & (arr["created_utc"] <= t)]

    def expected_vacant(self, room: Room, day, t: pd.Timestamp) -> pd.Timestamp | None:
        """When an occupied room that departs on ``day`` is expected to be vacant."""
        if room.booking is None or room.booking not in self.bookings.index:
            return None
        if self.bookings.at[room.booking, "dep_day"] > day:
            return None  # stay-over
        deadline = self.at_local(day, self.checkout_hm)
        return max(deadline, t + TOLERANCE)

    def tasks_at(
        self, t: pd.Timestamp, day, overrides: dict | None = None, firm: set | None = None
    ) -> list[Task]:
        """Rooms that still need a cleaner on ``day``.

        Occupied rooms departing today are expected to empty along the trailing distribution
        of checkout times (the house does not empty all at once). Rooms in ``firm`` are
        instead assumed to stay occupied until the checkout deadline: for a promise to one
        specific guest we plan with the only time that is guaranteed.
        """
        overrides, firm = overrides or {}, firm or set()
        departing = [
            rid
            for rid, room in self.rooms.items()
            if room.occupied
            and room.cleaning_by is None
            and rid not in overrides
            and rid not in firm
            and self.expected_vacant(room, day, t) is not None
        ]
        profile: dict[str, pd.Timestamp] = {}
        minutes = self.stats.checkout_minute
        if len(minutes) >= MIN_SAMPLES and departing:
            n = len(departing)
            qs = np.quantile(np.asarray(minutes), [(i + 0.5) / n for i in range(n)])
            for rid, q in zip(departing, qs, strict=True):
                guess = self.at_local(day, (int(q) // 60, int(q) % 60))
                latest = self.expected_vacant(self.rooms[rid], day, t)
                profile[rid] = min(max(guess, t + TOLERANCE), latest)
        tasks = []
        for rid, room in self.rooms.items():
            if room.cleaning_by is not None:
                continue  # already being cleaned
            if room.occupied:
                vacant = (
                    overrides.get(rid) or profile.get(rid) or self.expected_vacant(room, day, t)
                )
                if vacant is None:
                    continue
                robot_ready = None  # a future robot pass has not been reserved or observed
            elif room.dirty_since is not None:
                vacant = t
                if room.robot_done is not None:
                    robot_ready = room.robot_done
                elif room.robot_started is not None:
                    robot_ready = room.robot_started + pd.Timedelta(minutes=self.stats.robot_min)
                else:
                    robot_ready = None
            else:
                continue
            need, _ = self.need_for(rid, day, t)
            tasks.append(Task(rid, vacant, need, robot_ready))
        return tasks

    def ready_times(
        self, t: pd.Timestamp, day, policy: str, overrides=None, firm=None
    ) -> dict[str, Slot]:
        crew, _ = self.cleaners_at(t, day)
        slots = plan_queue(
            self.tasks_at(t, day, overrides, firm),
            crew,
            self.stats.clean_min,
            self.stats.clean_robot_min,
            policy,
        )
        for rid, room in self.rooms.items():
            if room.cleaning_by is not None and room.cleaning_started is not None:
                assisted = room.robot_done is not None and room.robot_done <= room.cleaning_started
                duration = self.stats.clean_robot_min if assisted else self.stats.clean_min
                end = room.cleaning_started + pd.Timedelta(minutes=duration)
                slots[rid] = Slot(room.cleaning_started, max(end, t), room.cleaning_by)
        return slots

    def free_clean_room(self, day, t: pd.Timestamp) -> str | None:
        """A vacant, clean room nobody is booked into today (candidate for a reassignment)."""
        for rid, room in self.rooms.items():
            if room.occupied or room.dirty_since is not None or room.cleaning_by is not None:
                continue
            if (rid, day) not in self.spare_offered and self.known_arrivals(rid, day, t).empty:
                return rid
        return None

    # ------------------------------------------------------------------ emitting

    def emit(self, at: pd.Timestamp, asset: str, action: str, episode: str, **fields) -> str:
        event_id = make_event_id(ISSUE, asset, at, action)
        n = sum(1 for d in self.decisions if d.event_id.startswith(event_id))
        if n:
            event_id = f"{event_id}:{n + 1}"
        self.decisions.append(
            Decision(
                event_id=event_id,
                episode_id=episode,
                timestamp_utc=at,
                issue_type=ISSUE,
                action=action,
                **fields,
            )
        )
        return event_id

    def episode(self, room: str, day) -> str:
        return f"{ISSUE}:{room}:{day:%Y%m%d}"

    def staff_ok(self, resource_id: str | None, at: pd.Timestamp) -> str | None:
        if not resource_id:
            return None
        if self.ds.known_absence(resource_id, at) or not self.ds.on_shift(resource_id, at):
            return None
        return resource_id

    def room_evidence(self, rid: str, at: pd.Timestamp) -> list[str]:
        room = self.rooms[rid]
        out = []
        if room.occupied:
            out.append(
                f"events.jsonl:{rid} still occupied by {room.booking} at {utc_z(at)} (no checkout event yet)"
            )
        elif room.dirty_since is not None:
            out.append(
                f"events.jsonl:{room.checkout_event}:checkout {rid} at {utc_z(room.dirty_since)}; no cleaning_completed since"
            )
        if room.cleaning_by:
            out.append(
                f"events.jsonl:cleaning_started {rid} by {room.cleaning_by} at {utc_z(room.cleaning_started)}"
            )
        return out

    def conflict(
        self,
        at: pd.Timestamp,
        rid: str,
        day,
        need: pd.Timestamp,
        eta: pd.Timestamp | None,
        slot: Slot,
        booking: str | None,
        language: str | None,
        request_id: str | None,
        absent: list[str],
        why: str,
        fifo_eta: pd.Timestamp | None,
    ) -> None:
        """Emit the linked decisions for one room-day conflict (plan, robot, guest message)."""
        episode = self.episode(rid, day)
        room = self.rooms[rid]
        evidence = self.room_evidence(rid, at)
        if booking:
            evidence.append(f"bookings.csv:{booking}:arrival {rid} on {day}")
        if request_id:
            evidence.append(f"guest_requests.csv:{request_id}")
        evidence += absent
        late = eta is None or eta > need
        eta_txt = _hhmm(_ceil_quarter(eta), self.tz) if eta is not None else None
        need_txt = _hhmm(need, self.tz)
        requests = [request_id] if request_id else None

        resource = self.staff_ok(slot.resource_id, at)
        plan_reason = f"{why} Guest needs {rid} at {need_txt} local; " + (
            f"with priority cleaning it is ready about {eta_txt}"
            if eta_txt
            else "no cleaner capacity is left in today's shift"
        )
        if fifo_eta is not None and eta is not None and fifo_eta > eta + TOLERANCE:
            plan_reason += (
                f" (in checkout order it would be {_hhmm(_ceil_quarter(fifo_eta), self.tz)})"
            )
        plan_reason += "."
        plan_id = self.emit(
            at,
            rid,
            "schedule_cleaning",
            episode,
            affected_assets=[rid] + ([resource] if resource else []),
            priority="high" if late else "medium",
            reason=plan_reason + " Move this room to the front of the cleaning queue.",
            requires_human_approval=False,
            resource_id=resource,
            evidence=evidence,
            guest_request_ids=requests,
            expected_outcome=(
                f"Simulated plan: cleaning finishes about {eta_txt} local; the room is released only after a cleaner completes it."
                if eta_txt
                else "Simulated plan: room cannot be finished by the regular shift; needs on-call cover."
            ),
            verification_plan=(
                f"cleaning_started for {rid} follows the checkout, cleaning_completed by {eta_txt or 'shift end'} local, "
                "housekeeping_state=clean before any check-in; escalate if not started within 30 min of the room being vacant."
            ),
            confidence=0.8 if late else 0.65,
        )

        robot = self.robot_on_shift(at)
        deps = [plan_id]
        if robot and not room.occupied and room.robot_started is None and room.cleaning_by is None:
            self.reserve_robot(robot, at)
            deps.append(
                self.emit(
                    at,
                    rid,
                    "dispatch_robot",
                    episode,
                    affected_assets=[rid, robot],
                    priority="medium",
                    reason=f"{rid} is vacant and on the critical path: robot floor clean first shortens the human cleaning "
                    f"(trailing median {self.stats.clean_robot_min:.0f} min after a robot pass, {self.stats.clean_min:.0f} min without).",
                    requires_human_approval=False,
                    resource_id=robot,
                    evidence=evidence[:1],
                    dependencies=[plan_id],
                    expected_outcome="Simulated: floor clean done before the cleaner arrives. The robot cannot certify or release the room.",
                    verification_plan=f"robot_floor_cleaning_completed for {rid} before cleaning_started.",
                    confidence=0.7,
                )
            )

        if late and booking and episode not in self.episodes_with_notice:
            self.episodes_with_notice.add(episode)
            lang = language or self.language_for(booking, at)
            shown = eta_txt or _hhmm(
                self.at_local(day, self.oncall_start_hm) + ONCALL_TRAVEL + pd.Timedelta(minutes=60),
                self.tz,
            )
            text, lang = render_message("eta", lang, eta=shown)
            self.emit(
                at,
                rid,
                "notify_guest",
                episode,
                affected_assets=[rid],
                priority="medium",
                reason=f"Guest {booking} expects {rid} at {need_txt} local but it will realistically be ready about {shown}. "
                "Tell them now rather than let them wait at the door.",
                requires_human_approval=False,
                evidence=evidence,
                dependencies=deps,
                guest_request_ids=requests,
                proposed_message=text,
                message_language=lang,
                expected_outcome="Proposed message only - not proof of delivery. Guest can plan arrival or leave luggage.",
                verification_plan=f"No arrival_waiting_room / waiting_for_room request for {rid} before {shown} local; "
                "send an update if cleaning_completed slips by more than 15 min.",
                confidence=0.7,
            )

    # ------------------------------------------------------------------ triggers

    def on_early_request(self, r) -> None:
        t = r.timestamp_utc
        if r.booking_id not in self.bookings.index:
            return
        b = self.bookings.loc[r.booking_id]
        day, rid = b["arr_day"], r.room_id
        if rid not in self.rooms:
            return
        if len(self.known_arrivals(rid, day, t)) != 1:
            return  # overlapping reservations need human reconciliation
        hm = parse_requested_time(r.text)
        if hm is None:
            return
        need = self.at_local(day, hm)
        if need >= self.at_local(day, self.checkin_hm) or need <= t:
            return
        room = self.rooms[rid]
        turns_today = room.dirty_since is not None or room.cleaning_by is not None
        if room.occupied and room.booking in self.bookings.index:
            turns_today = self.bookings.at[room.booking, "dep_day"] <= day
        if not turns_today:
            return  # vacant and clean: nothing to turn, routine early check-in
        watch = Watch(r.request_id, r.booking_id, rid, need, r.language, t)
        self.watches.append(watch)
        lead = pd.Timedelta(minutes=self.stats.robot_min + self.stats.clean_robot_min)
        check_at = max(t, need - lead, self.at_local(day, (PLAN_HOUR_LOCAL, 0)))
        self.schedule(check_at, "watch", watch)

    def on_watch(self, t: pd.Timestamp, w: Watch) -> None:
        if w.done:
            return
        w.done = True
        day = self.day_of(w.need)
        if len(self.known_arrivals(w.room, day, t)) != 1:
            return  # a later booking made the room assignment uncertain
        room = self.rooms[w.room]
        ref = [f"guest_requests.csv:{w.request_id}"]
        if not room.occupied and room.dirty_since is None and room.cleaning_by is None:
            audit.dismiss(
                ISSUE,
                w.room,
                t,
                f"Early check-in at {_hhmm(w.need, self.tz)} asked for a room with a same-day departure, but it was "
                "already cleaned in time: no conflict.",
                ref,
            )
            return
        if room.occupied and room.booking == w.booking:
            return  # guest already in
        edf = self.ready_times(t, day, "edf", firm={w.room}).get(w.room)
        fifo = self.ready_times(t, day, "fifo", firm={w.room}).get(w.room)
        _, absent = self.cleaners_at(t, day)
        eta = edf.end if edf else None
        fifo_eta = fifo.end if fifo else None
        if edf is None:
            return
        # A promise to a guest gets no tolerance: ready after the stated time is late.
        late = eta is None or eta > w.need - TOLERANCE  # under one step of slack = at risk
        fifo_late = fifo_eta is None or fifo_eta > w.need + TOLERANCE
        if not late and not fifo_late:
            audit.dismiss(
                ISSUE,
                w.room,
                t,
                f"Early check-in at {_hhmm(w.need, self.tz)} with a same-day departure: cleaning queue reaches the "
                f"room by {_hhmm(eta, self.tz)} without intervention.",
                ref + self.room_evidence(w.room, t),
            )
            return
        why = (
            "Previous guest has not checked out yet."
            if room.occupied
            else "Room is vacant but still dirty and behind other rooms in the queue."
        )
        self.conflict(
            t,
            w.room,
            day,
            w.need,
            eta,
            edf,
            w.booking,
            w.language,
            w.request_id,
            absent,
            why,
            fifo_eta,
        )

    def on_day_plan(self, t: pd.Timestamp) -> None:
        """Shift start: can today's 15:00 arrivals be served with the crew we know we have?"""
        day = self.day_of(t)
        crew, absent = self.cleaners_at(t, day)
        slots = self.ready_times(t, day, "edf")
        checkin = self.at_local(day, self.checkin_hm)
        short = []
        for rid, slot in slots.items():
            need, booking = self.need_for(rid, day, t)
            if need is None:
                continue
            if slot.end is None or slot.end > checkin + TOLERANCE:
                short.append((rid, slot, booking))
        if not short:
            return
        self.day_flagged.add(day)
        tasks = self.tasks_at(t, day)
        episode = f"{ISSUE}:BUILDING:{day:%Y%m%d}"
        short.sort(key=lambda x: (x[1].end is None, x[1].end, x[0]))
        rooms = [s[0] for s in short]
        order = sorted(
            (s for s in slots.items() if s[1].start is not None), key=lambda kv: kv[1].start
        )
        evidence = absent + [
            f"staff_roster.csv:{len(crew)} cleaner(s) on shift {day}: {', '.join(c.resource_id for c in crew) or 'none'}",
            f"bookings.csv:{len(tasks)} rooms to turn on {day}, {sum(1 for x in tasks if x.need is not None)} with a same-day arrival",
            f"events.jsonl:trailing median cleaning {self.stats.clean_robot_min:.0f} min with robot pre-clean, {self.stats.clean_min:.0f} min without",
        ]
        reason = (
            f"Capacity shortfall: {len(tasks)} rooms to turn with {len(crew)} cleaner(s)"
            + (" after a known absence" if absent else "")
            + f". Even in earliest-arrival-first order, {len(rooms)} room(s) with a guest arriving today are not ready by "
            f"{_hhmm(checkin, self.tz)}: {', '.join(rooms)}."
        )
        plan_id = self.emit(
            t,
            "BUILDING",
            "schedule_cleaning",
            episode,
            affected_assets=rooms + [c.resource_id for c in crew],
            priority="high",
            reason=reason
            + " Clean in this order: "
            + ", ".join(k for k, _ in order[:12])
            + (" ..." if len(order) > 12 else "")
            + ".",
            requires_human_approval=False,
            evidence=evidence,
            expected_outcome="Simulated plan: rooms with the earliest arrivals are cleaned first; stay-free rooms last. No room is released before a cleaner completes it.",
            verification_plan="cleaning_completed before check-in for the listed rooms; count of arrival_waiting_room events today; re-plan if another absence becomes known.",
            confidence=0.7,
        )
        robot = self.robot_on_shift(t)
        robot_room = next(
            (
                rid
                for rid in rooms
                if not self.rooms[rid].occupied
                and self.rooms[rid].dirty_since is not None
                and self.rooms[rid].robot_started is None
                and self.rooms[rid].cleaning_by is None
            ),
            None,
        )
        if robot and robot_room:
            self.reserve_robot(robot, t)
            self.emit(
                t,
                robot_room,
                "dispatch_robot",
                episode,
                affected_assets=[robot_room, robot],
                priority="medium",
                reason=f"Pre-clean the floor of vacant {robot_room}; this robot is reserved for one room at a time. "
                "Only a cleaner can certify the room as ready.",
                requires_human_approval=False,
                resource_id=robot,
                evidence=self.room_evidence(robot_room, t),
                dependencies=[plan_id],
                expected_outcome="Simulated: floor pre-clean for this room only; a cleaner still has to finish the room.",
                verification_plan=f"robot_floor_cleaning_completed for {robot_room} precedes cleaning_started.",
                confidence=0.6,
            )
        unfinished = [rid for rid, slot, _ in short if slot.end is None]
        oncall_etas = {}
        if unfinished:
            cursor = max(
                t + ONCALL_TRAVEL,
                self.at_local(day, self.oncall_start_hm) + ONCALL_TRAVEL,
                self.oncall_reserved_until.get(day, t),
            )
            for rid in unfinished:
                end = cursor + pd.Timedelta(minutes=self.stats.clean_min)
                if end > self.at_local(day, self.oncall_end_hm):
                    break
                oncall_etas[rid] = end
                cursor = end
        if unfinished:
            self.emit(
                t,
                "BUILDING",
                "request_human_approval",
                episode,
                affected_assets=["ONCALL_1"] + unfinished,
                priority="high",
                reason=f"{len(unfinished)} arrival room(s) cannot be cleaned within the regular shift ({', '.join(unfinished)}). "
                f"The single on-call housekeeper can cover {len(oncall_etas)} within the shift, one room at a time; "
                "approve that cover and arrange other capacity for the rest.",
                requires_human_approval=True,
                evidence=evidence,
                dependencies=[plan_id],
                expected_outcome="If approved (simulated): on-call covers only the rooms that fit its shift; any remainder needs another plan.",
                verification_plan="oncall_housekeeping_called event after approval; cleaning_completed for the listed rooms.",
                confidence=0.6,
            )
        for rid, slot, booking in short:
            if booking is None:
                continue
            ep = self.episode(rid, day)
            if ep in self.episodes_with_notice:
                continue
            self.episodes_with_notice.add(ep)
            eta = slot.end
            if eta is None:
                eta = oncall_etas.get(rid)
            if eta is None:
                continue  # no feasible capacity: do not promise a fabricated ready time
            shown = _hhmm(_ceil_quarter(eta), self.tz)
            text, lang = render_message("eta", self.language_for(booking, t), eta=shown)
            self.emit(
                t,
                rid,
                "notify_guest",
                ep,
                affected_assets=[rid],
                priority="medium",
                reason=f"Arrival {booking} in {rid} today, but with today's cleaning capacity the room is realistically ready about {shown} local.",
                requires_human_approval=False,
                evidence=[f"bookings.csv:{booking}:arrival {rid} on {day}"] + absent,
                dependencies=[plan_id],
                proposed_message=text,
                message_language=lang,
                expected_outcome="Proposed message only - not proof of delivery.",
                verification_plan=f"No arrival_waiting_room for {rid} before {shown} local; update the guest if cleaning slips.",
                confidence=0.6,
            )

    def on_late_checkout(self, r) -> None:
        t, rid = r.timestamp_utc, r.room_id
        hm = parse_requested_time(r.text)
        if hm is None or rid not in self.rooms or r.booking_id not in self.bookings.index:
            return
        room = self.rooms[rid]
        if not room.occupied or room.booking != r.booking_id:
            return
        day = self.bookings.at[r.booking_id, "dep_day"]
        asked = self.at_local(day, hm)
        need, next_booking = self.need_for(rid, day, t)
        if need is None or asked <= self.at_local(day, self.checkout_hm):
            return  # nobody arrives today: routine
        if self.day_of(t) != day:
            return
        slot = self.ready_times(t, day, "edf", overrides={rid: asked}).get(rid)
        eta = slot.end if slot else None
        ref = [
            f"guest_requests.csv:{r.request_id}",
            f"bookings.csv:{next_booking}:arrival {rid} on {day}",
        ]
        if eta is not None and eta <= need + TOLERANCE:
            audit.dismiss(
                ISSUE,
                rid,
                t,
                f"Late check-out until {_hhmm(asked, self.tz)} with a same-day arrival: room still ready "
                f"about {_hhmm(eta, self.tz)}, before the next guest needs it at {_hhmm(need, self.tz)}.",
                ref,
            )
            return
        lead = pd.Timedelta(minutes=self.stats.robot_min + self.stats.clean_robot_min)
        offer = max((need - lead).floor("15min"), self.at_local(day, self.checkout_hm))
        text, lang = render_message(
            "late_checkout", r.language, asked=_hhmm(asked, self.tz), offer=_hhmm(offer, self.tz)
        )
        _, absent = self.cleaners_at(t, day)
        self.emit(
            t,
            rid,
            "notify_guest",
            self.episode(rid, day),
            affected_assets=[rid],
            priority="medium",
            reason=f"Late check-out until {_hhmm(asked, self.tz)} would leave {rid} ready "
            + (
                f"about {_hhmm(_ceil_quarter(eta), self.tz)}"
                if eta is not None
                else "after the cleaning shift"
            )
            + f", but guest {next_booking} needs it at {_hhmm(need, self.tz)} local. Offer {_hhmm(offer, self.tz)} instead.",
            requires_human_approval=False,
            evidence=ref + absent,
            guest_request_ids=[r.request_id],
            proposed_message=text,
            message_language=lang,
            expected_outcome="Proposed message only - not proof of delivery. Protects the next guest's check-in time.",
            verification_plan=f"checkout for {rid} by {_hhmm(offer, self.tz)} local and cleaning_completed before {_hhmm(need, self.tz)}.",
            confidence=0.7,
        )

    def on_waiting(self, t: pd.Timestamp, rid: str, booking: str | None, request=None) -> None:
        """A guest is at the door (event) or asks where the room is (request)."""
        if rid not in self.rooms:
            return
        day = self.day_of(t)
        room = self.rooms[rid]
        if room.occupied and room.booking == booking:
            return
        episode = self.episode(rid, day)
        self.waiting.setdefault((rid, day), t)
        slot = self.ready_times(t, day, "edf").get(rid)
        eta = slot.end if slot else None
        evidence = self.room_evidence(rid, t)
        language = "en"
        requests = None
        if request is not None:
            evidence.append(f"guest_requests.csv:{request.request_id}")
            language, requests = request.language, [request.request_id]
        else:
            evidence.append(f"events.jsonl:arrival_waiting_room {rid} {booking} at {utc_z(t)}")
            language = self.language_for(booking, t)
        deps = []
        if eta is None and room.cleaning_by is None and room.dirty_since is not None:
            slot_oncall = self.oncall_slot(day, t)
            if slot_oncall and not any(
                d.episode_id == episode and d.resource_id == "ONCALL_1" for d in self.decisions
            ):
                _, eta = slot_oncall
                self.oncall_reserved_until[day] = eta
                self.oncall_eta_by_episode[episode] = eta
                deps.append(
                    self.emit(
                        t,
                        rid,
                        "schedule_cleaning",
                        episode,
                        affected_assets=[rid, "ONCALL_1"],
                        priority="critical",
                        reason=f"Guest {booking} is waiting for {rid}, the room is not clean and no regular cleaner has capacity left today. "
                        "Call the on-call housekeeper (costly, ~45 min to site) - needs approval.",
                        requires_human_approval=True,
                        resource_id="ONCALL_1",
                        evidence=evidence,
                        guest_request_ids=requests,
                        expected_outcome=f"If approved (simulated): room cleaned and released about {_hhmm(_ceil_quarter(eta), self.tz)} local.",
                        verification_plan=f"oncall_housekeeping_called and cleaning_completed for {rid}; escalate to the duty manager if not approved within 15 min.",
                        confidence=0.75,
                    )
                )
            elif episode in self.oncall_eta_by_episode:
                eta = self.oncall_eta_by_episode[episode]
        long_wait = eta is None or eta - t > LONG_WAIT
        already = any(
            d.episode_id == episode and d.action == "reassign_room" for d in self.decisions
        )
        spare = self.free_clean_room(day, t) if long_wait and not already else None
        if spare:
            self.spare_offered.add((spare, day))
            deps.append(
                self.emit(
                    t,
                    rid,
                    "reassign_room",
                    episode,
                    affected_assets=[rid, spare],
                    priority="high",
                    reason=f"Guest {booking} is waiting for {rid}, which will not be ready "
                    + (
                        f"before about {_hhmm(_ceil_quarter(eta), self.tz)} local"
                        if eta is not None
                        else "today"
                    )
                    + f". {spare} is vacant, clean and not booked for an arrival today: propose moving the guest (needs approval).",
                    requires_human_approval=True,
                    evidence=evidence
                    + [
                        f"events.jsonl:{spare} vacant and clean at {utc_z(t)}; bookings.csv: no arrival in {spare} on {day}"
                    ],
                    guest_request_ids=requests,
                    expected_outcome=f"If approved (simulated): guest checks into {spare} now instead of waiting; {rid} is cleaned in the normal queue.",
                    verification_plan=f"room_reassigned and check-in event for {booking} in {spare}; confirm room type suits the party before approving.",
                    confidence=0.6,
                )
            )
        if eta is None:
            return
        shown = _hhmm(_ceil_quarter(max(eta, t + TOLERANCE)), self.tz)
        text, lang = render_message("waiting", language, eta=shown)
        self.episodes_with_notice.add(episode)
        self.emit(
            t,
            rid,
            "notify_guest",
            episode,
            affected_assets=[rid],
            priority="high",
            reason=f"Guest {booking} is waiting for {rid}; give a realistic ready time ({shown} local) instead of silence.",
            requires_human_approval=False,
            evidence=evidence,
            dependencies=deps or None,
            guest_request_ids=requests,
            proposed_message=text,
            message_language=lang,
            expected_outcome="Proposed message only - not proof of delivery.",
            verification_plan=f"cleaning_completed for {rid} by {shown} local, then check-in; send an update if it slips.",
            confidence=0.7,
        )

    # --------------------------------------------------------------- event loop

    def schedule(self, at: pd.Timestamp, kind: str, payload) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (at.value, self._order[kind], self._seq, kind, payload))

    def apply_event(self, e) -> None:
        rid = e.room_id
        if not isinstance(rid, str) or rid not in self.rooms:
            return
        room, t, kind = self.rooms[rid], e.timestamp_utc, e.type
        if kind in ("stay_in_progress", "checkin", "checkin_early", "checkin_after_wait"):
            self.rooms[rid] = Room(occupied=True, booking=e.booking_id)
        elif kind in ("checkout", "checkout_early"):
            self.rooms[rid] = Room(dirty_since=t, checkout_event=e.event_id)
            if kind == "checkout":
                local = t.tz_convert(self.tz)
                self.stats.checkout_minute.append(local.hour * 60 + local.minute)
        elif kind == "robot_floor_cleaning_started":
            room.robot_started, room.robot_by = t, e.resource_id
        elif kind == "robot_floor_cleaning_completed":
            room.robot_done = t
            if room.robot_started is not None:
                self.stats.robot.append((t - room.robot_started) / pd.Timedelta(minutes=1))
        elif kind == "cleaning_started":
            room.cleaning_by, room.cleaning_started = e.resource_id, t
        elif kind == "cleaning_completed":
            if room.cleaning_started is not None:
                minutes = (t - room.cleaning_started) / pd.Timedelta(minutes=1)
                if 0 < minutes < 240:
                    assisted = (
                        room.robot_done is not None and room.robot_done <= room.cleaning_started
                    )
                    (self.stats.clean_robot if assisted else self.stats.clean).append(minutes)
            self.rooms[rid] = Room()

    def run(self, weekend_callback=None) -> list[Decision]:
        ds = self.ds
        self._heap: list = []
        self._seq = 0
        # At one timestamp: state first, then requests, then our own checks.
        self._order = {"event": 0, "request": 1, "watch": 2, "weekend": 3, "plan": 4}

        first = ds.room_telemetry.sort_values("timestamp_utc").drop_duplicates("room_id")
        for r in first.itertuples():
            if r.room_id in self.rooms and r.housekeeping_state == "dirty":
                self.rooms[r.room_id].dirty_since = r.timestamp_utc

        ev = ds.events
        ev = ev[ev["room_id"].notna()]
        for e in ev.itertuples():
            self.schedule(e.timestamp_utc, "event", e)
        req = ds.guest_requests
        req = req[req["request_type"].isin(["early_checkin", "late_checkout", "waiting_for_room"])]
        for r in req.itertuples():
            self.schedule(r.timestamp_utc, "request", r)
        first_day = ds.start.tz_convert(self.tz).date()
        last_day = ds.end.tz_convert(self.tz).date()
        for day in pd.date_range(first_day, last_day, freq="D"):  # calendar days: DST-safe
            at = self.at_local(day.date(), (PLAN_HOUR_LOCAL, 0))
            if ds.start <= at < ds.end:
                self.schedule(at, "plan", None)
                if weekend_callback and day.weekday() >= 5:
                    self.schedule(at, "weekend", day.date())
        if weekend_callback:
            roster = ds.staff_roster
            for r in roster[
                (roster["role"] == "cleaner") & (roster["status"] != "scheduled")
            ].itertuples():
                day = r.start_utc.tz_convert(self.tz).date()
                if day.weekday() >= 5 and ds.start <= r.status_known_utc < ds.end:
                    self.schedule(r.status_known_utc, "weekend", day)

        while self._heap:
            value, _, _, kind, payload = heapq.heappop(self._heap)
            t = pd.Timestamp(value, tz="UTC")
            if kind == "event":
                if payload.type == "arrival_waiting_room":
                    ep = self.episode(payload.room_id, self.day_of(t))
                    if ep not in self.episodes_with_notice:
                        self.on_waiting(t, payload.room_id, payload.booking_id)
                else:
                    self.apply_event(payload)
            elif kind == "request":
                if payload.request_type == "early_checkin":
                    self.on_early_request(payload)
                elif payload.request_type == "late_checkout":
                    self.on_late_checkout(payload)
                else:
                    self.on_waiting(t, payload.room_id, payload.booking_id, payload)
                if weekend_callback and payload.booking_id in self.bookings.index:
                    day = self.bookings.at[payload.booking_id, "arr_day"]
                    if day.weekday() >= 5 and t <= self.at_local(day, self.checkin_hm):
                        weekend_callback(self, t, day)
            elif kind == "watch":
                self.on_watch(t, payload)
            elif kind == "weekend":
                weekend_callback(self, t, payload)
            elif kind == "plan":
                self.on_day_plan(t)
        return self.decisions


def detect(ds: Dataset) -> list[Decision]:
    if ds.__dict__.get("_disable_weekend_coordination"):
        decisions = _Replay(ds).run()
    else:
        from steward.detectors.occupancy_surge import coordinated_replay

        decisions = coordinated_replay(ds)[0]
    assert not any(math.isnan(d.confidence or 0.0) for d in decisions)
    return decisions
