"""S03 Energy vs Comfort (Level 1): reduced heat-pump performance meeting occupied rooms.

How it works (everything is causal - a decision at t uses only rows stamped <= t and
forecasts / tariffs / bookings / roster facts already published at t):

1. Plant health. HP1's efficiency (heat_output / electric) is an almost linear function of
   outdoor temperature. Every day we refit that line on the plant's OWN trailing history and
   compare the last 3 h against it. A sustained drop below the line is degradation (in the
   data: a refrigerant leak, ended by a contractor visit). No fixed COP numbers are used.
2. Faults. Any status other than running / standby / defrost is a fault code. One isolated,
   self-clearing fault with normal efficiency is logged as dismissed; repeated faults open
   an incident. Defrost is normal operation and never counts.
3. Room impact. An occupied room counts as cold only if it is below the comfort band AND
   well below its own setpoint, with the window closed, a plausible live sensor, and the
   guest in the room for longer than the usual warm-up after check-in. The count is compared
   with the building's own trailing normal, so a structurally cool floor is not an incident.
4. One episode per plant incident: flag, organise service (approval), comfort-first energy
   plan from forecast + tariff, pre-heat arriving rooms, and - only if rooms really get
   cold - escalate, shed vacant-room load and tell the affected guests.

Guest complaints about cold outside a plant incident are triaged and logged via
``steward.audit`` (typically: room still warming up right after check-in, or window open).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from steward import audit
from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "heating_comfort"
PLANT = "HP1"
NORMAL_STATUSES = {"running", "standby", "defrost"}

# --- plant baseline -------------------------------------------------------------------
HISTORY = pd.Timedelta(days=120)  # trailing window the efficiency line is learned from
HEATING_REGIME_MAX_C = 15.0  # above this the plant mostly makes hot water; COP is noisy
MIN_LOAD_KW = 3.0  # ignore near-idle samples (ratio of two small numbers)
MIN_FIT_SAMPLES = 192  # two days of 15-min samples before we trust a baseline
MIN_FIT_SPAN_C = 4.0  # ...covering at least this outdoor range
WINDOW = pd.Timedelta(hours=3)  # efficiency is judged on a 3 h median
MIN_WINDOW_SAMPLES = 6
MIN_DROP = 0.06  # never call less than a 6 % efficiency loss degradation
SIGMA_FACTOR = 5.0  # ...and require 5 sigma of the plant's own window-median noise
EXTRAPOLATION_MARGIN = 0.03  # be stricter when colder than anything in the history
SUSTAIN_HOURS = 3  # consecutive hourly checks below the line to open an incident
RECOVER_HOURS = 6  # consecutive healthy hourly checks to close it
FAULT_WINDOW = pd.Timedelta(hours=24)
FAULTS_TO_OPEN = 2  # this many fault samples within FAULT_WINDOW open an incident

# --- room impact ----------------------------------------------------------------------
SETPOINT_GAP_K = 1.0  # "cold" = below the band and at least this far below setpoint
WARMUP_AFTER_CHECKIN = pd.Timedelta(hours=3)  # rooms start from the 18 C setback
COLD_SUSTAIN_SAMPLES = 4  # one hour of consecutive cold readings
FROZEN_SAMPLES = 8  # identical readings for 2 h = stuck sensor
PLAUSIBLE_ROOM_C = (5.0, 40.0)
NORMAL_WINDOW = pd.Timedelta(days=14)  # trailing window for "normal" number of cold rooms
MIN_COLD_ROOMS = 3  # impact needs at least this many rooms...
MARGIN_OVER_NORMAL = 2  # ...and this many more than the trailing 95th percentile
MAX_ROOMS_LISTED = 8

# --- planning -------------------------------------------------------------------------
LOOKAHEAD = pd.Timedelta(hours=24)
PREHEAT_HORIZON = pd.Timedelta(hours=6)  # start heating arriving rooms this early
SHED_HORIZON = pd.Timedelta(hours=12)  # vacant rooms with no arrival this far ahead can be lowered
NOTIFY_BELOW_BAND_K = 0.5  # tell guests proactively only when clearly below the band
VACANT_SHED_SETPOINT_C = 16.0
FORECAST_WORSE_K = 3.0  # re-plan only if the forecast minimum drops by this much

_MESSAGES = {
    "proactive": {
        "en": "Dear guest, our heating system is currently running at reduced output and your room ({room}) is cooler than it should be. We are sorry. Service has been requested and we are prioritising heat to occupied rooms. Extra blankets are available on request, and we can offer another room if you prefer.",
        "de": "Liebe Gäste, unsere Heizungsanlage läuft derzeit mit verminderter Leistung und Ihr Zimmer ({room}) ist kühler als vorgesehen. Wir bitten um Entschuldigung. Der Service ist angefordert und belegte Zimmer werden bevorzugt beheizt. Zusätzliche Decken erhalten Sie auf Wunsch; gerne bieten wir Ihnen auch ein anderes Zimmer an.",
        "it": "Gentile ospite, il nostro impianto di riscaldamento funziona attualmente a potenza ridotta e la sua camera ({room}) è più fredda del previsto. Ci scusiamo. L'assistenza è stata richiesta e stiamo dando priorità alle camere occupate. Coperte aggiuntive sono disponibili su richiesta e possiamo offrirle un'altra camera.",
        "fr": "Cher client, notre installation de chauffage fonctionne actuellement à puissance réduite et votre chambre ({room}) est plus fraîche que prévu. Nous vous prions de nous excuser. Une intervention a été demandée et les chambres occupées sont chauffées en priorité. Des couvertures supplémentaires sont disponibles sur demande et nous pouvons vous proposer une autre chambre.",
        "es": "Estimado huésped: nuestro sistema de calefacción funciona actualmente con potencia reducida y su habitación ({room}) está más fría de lo debido. Le pedimos disculpas. Se ha solicitado el servicio técnico y damos prioridad a las habitaciones ocupadas. Hay mantas adicionales a su disposición y podemos ofrecerle otra habitación.",
        "nl": "Beste gast, onze verwarmingsinstallatie werkt momenteel op verminderd vermogen en uw kamer ({room}) is koeler dan bedoeld. Onze excuses. Service is aangevraagd en bezette kamers krijgen voorrang. Extra dekens zijn op verzoek beschikbaar en we kunnen u een andere kamer aanbieden.",
    },
    "reply": {
        "en": "Thank you for telling us, and sorry about the cold room ({room}). The cause is a fault in the building's heating system, not your thermostat. Service has been requested and we are prioritising heat to occupied rooms. We can bring extra blankets or offer another room - just reply to this message.",
        "de": "Danke für Ihre Nachricht, und entschuldigen Sie das kalte Zimmer ({room}). Die Ursache ist eine Störung der Heizungsanlage des Hauses, nicht Ihr Thermostat. Der Service ist angefordert und belegte Zimmer werden bevorzugt beheizt. Wir bringen gerne zusätzliche Decken oder bieten Ihnen ein anderes Zimmer an - antworten Sie einfach auf diese Nachricht.",
        "it": "Grazie per la segnalazione e ci scusiamo per la camera fredda ({room}). La causa è un guasto all'impianto di riscaldamento dell'edificio, non il suo termostato. L'assistenza è stata richiesta e stiamo dando priorità alle camere occupate. Possiamo portare coperte aggiuntive o offrirle un'altra camera: basta rispondere a questo messaggio.",
        "fr": "Merci de nous avoir prévenus, et toutes nos excuses pour la chambre froide ({room}). La cause est une panne de l'installation de chauffage du bâtiment, et non votre thermostat. Une intervention a été demandée et les chambres occupées sont chauffées en priorité. Nous pouvons apporter des couvertures ou vous proposer une autre chambre : répondez simplement à ce message.",
        "es": "Gracias por avisarnos y disculpe el frío en la habitación ({room}). La causa es una avería en el sistema de calefacción del edificio, no su termostato. Se ha solicitado el servicio técnico y damos prioridad a las habitaciones ocupadas. Podemos llevarle mantas adicionales u ofrecerle otra habitación: solo responda a este mensaje.",
        "nl": "Dank voor uw bericht en excuses voor de koude kamer ({room}). De oorzaak is een storing in de verwarmingsinstallatie van het gebouw, niet uw thermostaat. Service is aangevraagd en bezette kamers krijgen voorrang. We brengen graag extra dekens of bieden u een andere kamer aan - beantwoord gewoon dit bericht.",
    },
}


# ------------------------------------------------------------------------------- helpers


def fit_efficiency_line(outdoor: np.ndarray, cop: np.ndarray) -> tuple[float, float, float] | None:
    """Robust line cop = a + b * outdoor. Returns (a, b, relative sigma) or None if too thin."""
    if len(cop) < MIN_FIT_SAMPLES or np.ptp(outdoor) < MIN_FIT_SPAN_C:
        return None
    b, a = np.polyfit(outdoor, cop, 1)
    ratio = cop / (a + b * outdoor)
    keep = ratio > 1 - MIN_DROP  # drop past degraded spells so they cannot become "normal"
    if keep.sum() >= MIN_FIT_SAMPLES and np.ptp(outdoor[keep]) >= MIN_FIT_SPAN_C:
        b, a = np.polyfit(outdoor[keep], cop[keep], 1)
        ratio = cop[keep] / (a + b * outdoor[keep])
    sigma = 1.4826 * np.median(np.abs(ratio - np.median(ratio)))
    return float(a), float(b), float(sigma)


def degradation_threshold(sigma: float) -> float:
    """Efficiency ratio below which the 3 h median counts as degraded."""
    window_sigma = 1.25 * sigma / np.sqrt(MIN_WINDOW_SAMPLES * 2)
    return 1.0 - max(MIN_DROP, SIGMA_FACTOR * window_sigma)


def _plant_hourly(ds: Dataset) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Hourly plant view (ratio vs causal baseline, threshold) and all fault sample times."""
    hp = ds.equipment_wide(PLANT)
    for col in ("heat_output_kw", "electric_kw", "status"):
        if col not in hp.columns:
            hp[col] = np.nan
    weather = ds.weather.drop_duplicates("timestamp_utc").set_index("timestamp_utc")
    hp = hp.join(weather["outdoor_c"]).sort_index()
    hp["cop"] = hp["heat_output_kw"] / hp["electric_kw"].where(hp["electric_kw"] >= MIN_LOAD_KW)
    hp["valid"] = (
        (hp["status"] == "running")
        & hp["cop"].between(0.5, 8.0)
        & hp["outdoor_c"].le(HEATING_REGIME_MAX_C)
    )
    hp["fault"] = hp["status"].notna() & ~hp["status"].isin(NORMAL_STATUSES)

    hp["expected"] = np.nan
    hp["threshold"] = np.nan
    hp["train_min_c"] = np.nan
    hp["demand_a"] = np.nan
    hp["demand_b"] = np.nan
    days = hp.index.normalize()
    valid = hp[hp["valid"]]
    for day in days.unique():
        hist = valid[(valid.index < day) & (valid.index >= day - HISTORY)]
        fit = fit_efficiency_line(hist["outdoor_c"].to_numpy(), hist["cop"].to_numpy())
        if fit is None:
            continue
        a, b, sigma = fit
        today = days == day
        hp.loc[today, "expected"] = a + b * hp.loc[today, "outdoor_c"]
        hp.loc[today, "threshold"] = degradation_threshold(sigma)
        hp.loc[today, "train_min_c"] = hist["outdoor_c"].min()
        db, da = np.polyfit(hist["outdoor_c"], hist["heat_output_kw"], 1)
        hp.loc[today, ["demand_a", "demand_b"]] = [da, db]

    hp["ratio"] = (hp["cop"] / hp["expected"]).where(hp["valid"])
    roll = hp["ratio"].rolling(WINDOW, min_periods=MIN_WINDOW_SAMPLES)
    hp["ratio_med"] = roll.median()
    hp["cop_med"] = (
        hp["cop"].where(hp["valid"]).rolling(WINDOW, min_periods=MIN_WINDOW_SAMPLES).median()
    )
    hp["heat_max"] = hp["heat_output_kw"].rolling(pd.Timedelta(hours=6), min_periods=1).max()
    hp["faults_24h"] = hp["fault"].astype(int).rolling(FAULT_WINDOW, min_periods=1).sum()
    return hp[hp.index.minute == 0], hp.index[hp["fault"]]


def _room_state(ds: Dataset) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Per 15-min sample and room: sustained-cold flag, temperature deficit, occupancy."""
    tel = ds.room_telemetry.drop_duplicates(["timestamp_utc", "room_id"])
    tel = ds.add_occupancy(tel)
    checkins = ds.lifecycle.loc[ds.lifecycle["occupied"], ["timestamp_utc", "room_id"]]
    checkins = checkins.rename(columns={"timestamp_utc": "checkin_utc"}).sort_values("checkin_utc")
    tel = pd.merge_asof(
        tel.sort_values("timestamp_utc"),
        checkins,
        left_on="timestamp_utc",
        right_on="checkin_utc",
        by="room_id",
    )
    settled = (tel["timestamp_utc"] - tel["checkin_utc"]) >= WARMUP_AFTER_CHECKIN

    def wide(col: str) -> pd.DataFrame:
        return tel.pivot(index="timestamp_utc", columns="room_id", values=col)

    temp = wide("temperature_c")
    low, _ = ds.comfort_band_c
    plausible = temp.ge(PLAUSIBLE_ROOM_C[0]) & temp.le(PLAUSIBLE_ROOM_C[1])
    frozen = temp.rolling(FROZEN_SAMPLES, min_periods=FROZEN_SAMPLES).std().eq(0)
    window_closed = wide("window_open").eq(0)  # unknown window state is not "closed"
    window_closed &= window_closed.shift(1, fill_value=True)
    occupied = wide("occupied").eq(True)
    deficit = wide("setpoint_c") - temp
    tel["settled"] = settled
    cold_now = (
        occupied
        & wide("settled").eq(True)
        & plausible
        & ~frozen
        & window_closed
        & temp.lt(low)
        & deficit.ge(SETPOINT_GAP_K)
    )
    cold = (
        cold_now.rolling(COLD_SUSTAIN_SAMPLES, min_periods=COLD_SUSTAIN_SAMPLES)
        .sum()
        .eq(COLD_SUSTAIN_SAMPLES)
    )
    return cold, low - temp, occupied


@dataclass
class _Episode:
    start: pd.Timestamp
    cause: str
    id: str = ""
    healthy_hours: int = 0
    impact_declared: bool = False
    plan_forecast_min: float | None = None
    preheat_days: set = field(default_factory=set)
    notified: set = field(default_factory=set)
    answered: set = field(default_factory=set)
    heat_cap_kw: float = 0.0
    flag_event: str = ""


class _Context:
    """Lookups of what was known at a given moment."""

    def __init__(self, ds: Dataset):
        self.ds = ds
        self.forecast = ds.weather_forecast.dropna(subset=["outdoor_c_forecast"]).sort_values(
            "issued_utc"
        )
        self.tariff = ds.tariff.dropna(subset=["price_eur_kwh"])
        req = ds.guest_requests
        self.requests = req.sort_values("timestamp_utc")
        self.cold_requests = self.requests[self.requests["request_type"] == "temperature_cold"]
        self.bookings = ds.bookings_expected  # no hindsight about cancellations

    def forecast_min(self, at: pd.Timestamp) -> tuple[float, pd.Timestamp, pd.Timestamp] | None:
        known = self.forecast[self.forecast["issued_utc"] <= at]
        if known.empty:
            return None
        issue = known["issued_utc"].iloc[-1]
        ahead = known[
            (known["issued_utc"] == issue) & known["target_utc"].between(at, at + LOOKAHEAD)
        ]
        if ahead.empty:
            return None
        row = ahead.loc[ahead["outdoor_c_forecast"].idxmin()]
        return float(row["outdoor_c_forecast"]), row["target_utc"], issue

    def price_plan(self, at: pd.Timestamp) -> dict | None:
        known = self.tariff[
            (self.tariff["published_utc"] <= at)
            & self.tariff["timestamp_utc"].between(at, at + LOOKAHEAD)
        ]
        if len(known) < 6:
            return None
        peak = known.loc[known["price_eur_kwh"].idxmax()]
        before_peak = known[known["timestamp_utc"] < peak["timestamp_utc"]]
        cheap = (before_peak if len(before_peak) else known).nsmallest(1, "price_eur_kwh").iloc[0]
        return {
            "peak_at": peak["timestamp_utc"],
            "peak": float(peak["price_eur_kwh"]),
            "cheap_at": cheap["timestamp_utc"],
            "cheap": float(cheap["price_eur_kwh"]),
        }

    def language(self, booking_id, at: pd.Timestamp) -> str | None:
        if booking_id is None or pd.isna(booking_id):
            return None
        mine = self.requests[
            (self.requests["booking_id"] == booking_id) & (self.requests["timestamp_utc"] <= at)
        ]
        langs = [x for x in mine["language"].dropna() if x in _MESSAGES["reply"]]
        return langs[-1] if langs else None

    def arrivals(self, at: pd.Timestamp, horizon: pd.Timedelta) -> list[str]:
        b = self.bookings
        soon = b[(b["created_utc"] <= at) & b["arrival_utc"].between(at, at + horizon)]
        return sorted(set(soon["room_id"].dropna()))

    def technician(self, at: pd.Timestamp) -> tuple[str | None, str]:
        """(resource to dispatch now or None, human-readable availability note)."""
        techs = [
            r["resource_id"]
            for r in self.ds.building["staff"]["resources"]
            if r["role"] == "technician"
        ]
        for tech in techs:
            absence = self.ds.known_absence(tech, at)
            if absence is None and self.ds.on_shift(tech, at):
                return tech, f"{tech} is on shift"
        roster = self.ds.staff_roster
        nxt = roster[
            roster["resource_id"].isin(techs)
            & (roster["start_utc"] > at)
            & (roster["status_known_utc"] <= at)
        ].sort_values("start_utc")
        nxt = nxt[nxt["status"] == "scheduled"]
        when = (
            f"next known shift starts {utc_z(nxt['start_utc'].iloc[0])}"
            if len(nxt)
            else "no upcoming shift known"
        )
        return None, f"in-house technician not available now ({when})"


def _message(kind: str, room: str, lang: str | None) -> tuple[str, str]:
    if lang:
        return _MESSAGES[kind][lang].format(room=room), lang
    text = (
        _MESSAGES[kind]["en"].format(room=room) + "\n\n" + _MESSAGES[kind]["de"].format(room=room)
    )
    return text, "en+de"


# ------------------------------------------------------------------------------ detector


def detect(ds: Dataset) -> list[Decision]:
    plant, fault_times = _plant_hourly(ds)
    if plant.empty:
        return []
    cold, below_band, occupied = _room_state(ds)
    cold_count = cold.sum(axis=1)
    hourly_count = cold_count[cold_count.index.minute == 0]
    normal_cold = (
        hourly_count.shift(1)
        .rolling(NORMAL_WINDOW, min_periods=24)
        .quantile(0.95)
        .reindex(plant.index)
        .fillna(0.0)
    )
    ctx = _Context(ds)
    stay_ids = ds.add_occupancy(
        ds.room_telemetry.loc[
            ds.room_telemetry["timestamp_utc"].dt.minute == 0, ["timestamp_utc", "room_id"]
        ]
    ).set_index(["timestamp_utc", "room_id"])["stay_booking_id"]

    decisions: list[Decision] = []
    episode: _Episode | None = None
    below_streak = 0
    recent_faults: list[pd.Timestamp] = []  # fault samples since the last incident closed
    handled_requests: set[str] = set()
    prev_at = plant.index[0] - pd.Timedelta(hours=1)

    def emit(ep: _Episode, at, action, assets, priority, reason, *, approval=False, **extra) -> str:
        event_id = make_event_id(ISSUE, assets[0], extra.get("stamp", at), action)
        if suffix := extra.pop("suffix", None):
            event_id += f":{suffix}"
        at = extra.pop("stamp", at)
        decisions.append(
            Decision(
                event_id=event_id,
                episode_id=ep.id,
                timestamp_utc=at,
                affected_assets=assets,
                issue_type=ISSUE,
                action=action,
                priority=priority,
                reason=reason,
                requires_human_approval=approval,
                **extra,
            )
        )
        return event_id

    for at, row in plant.iterrows():
        ratio, thr = row["ratio_med"], row["threshold"]
        strict = thr - (EXTRAPOLATION_MARGIN if row["outdoor_c"] < row["train_min_c"] else 0.0)
        degraded_now = pd.notna(ratio) and pd.notna(thr) and ratio < strict
        healthy_now = pd.notna(ratio) and pd.notna(thr) and ratio >= thr
        below_streak = below_streak + 1 if degraded_now else (below_streak if pd.isna(ratio) else 0)
        new_faults = list(fault_times[(fault_times > prev_at) & (fault_times <= at)])
        cold_rooms = list(cold.columns[cold.loc[at]]) if at in cold.index else []
        cold_rooms.sort(key=lambda r: -below_band.at[at, r])
        normal = float(normal_cold.at[at])
        impact = len(cold_rooms) >= max(MIN_COLD_ROOMS, normal + MARGIN_OVER_NORMAL)

        new_requests = ctx.cold_requests[
            (ctx.cold_requests["timestamp_utc"] > prev_at)
            & (ctx.cold_requests["timestamp_utc"] <= at)
        ]
        prev_at = at

        # ---- a fault that never became an incident within 24 h is a look-alike
        if episode is None:
            recent_faults.extend(new_faults)
            for fault_at in [f for f in recent_faults if at - f >= FAULT_WINDOW]:
                recent_faults.remove(fault_at)
                audit.dismiss(
                    ISSUE,
                    PLANT,
                    fault_at,
                    "Single heat-pump fault code that cleared by itself; efficiency stayed on its "
                    "normal line and no further fault followed within 24 h.",
                    [f"equipment.csv:{PLANT}:status@{utc_z(fault_at)}"],
                )
        faults = len(recent_faults)

        # ---- open an incident
        if episode is None and (below_streak >= SUSTAIN_HOURS or faults >= FAULTS_TO_OPEN):
            cause = (
                "degraded efficiency" if below_streak >= SUSTAIN_HOURS else "repeated fault codes"
            )
            episode = _Episode(start=at, cause=cause)
            episode.id = f"{ISSUE}:{PLANT}:{at.strftime('%Y%m%dT%H%M')}"
            recent_faults.clear()
            _open(episode, at, row, ratio, thr, faults, impact, cold_rooms, ctx, emit)
            if impact:  # rooms are already cold: the service call above is the urgent one
                episode.impact_declared = True
                _escalate(episode, at, row, cold_rooms, normal, occupied, ctx, emit, service=False)

        if episode is None:
            for _, req in new_requests.iterrows():
                _triage_complaint(ds, req, cold, below_band, occupied, handled_requests)
            continue

        # ---- inside an incident
        ep = episode
        if pd.notna(row["heat_max"]):
            ep.heat_cap_kw = max(ep.heat_cap_kw, float(row["heat_max"]))
        _plan_energy(ep, at, row, ctx, emit)
        _preheat_arrivals(ep, at, occupied, ctx, emit)

        if impact and not ep.impact_declared:
            ep.impact_declared = True
            _escalate(ep, at, row, cold_rooms, normal, occupied, ctx, emit, service=True)
        if ep.impact_declared:
            newly = []
            for room in cold_rooms:
                booking = stay_ids.get((at, room))
                if (room, booking) in ep.notified or below_band.at[at, room] < NOTIFY_BELOW_BAND_K:
                    continue
                ep.notified.add((room, booking))
                newly.append((room, ctx.language(booking, at)))
            for lang in sorted({lg or "" for _, lg in newly}):
                rooms = [r for r, lg in newly if (lg or "") == lang]
                _notify_proactive(ep, at, rooms, lang or None, below_band.loc[at, rooms], emit)
        for _, req in new_requests.iterrows():
            handled_requests.add(req["request_id"])
            _reply(ep, at, req, emit)

        # ---- close it once the plant is back on its line
        ep.healthy_hours = ep.healthy_hours + 1 if (healthy_now and not new_faults) else 0
        if ep.healthy_hours >= RECOVER_HOURS:
            episode, below_streak = None, 0
            recent_faults.clear()

    return decisions


def _open(ep, at, row, ratio, thr, faults, impact, cold_rooms, ctx, emit) -> None:
    eff = (
        f"3 h median efficiency (heat/electric) {row['cop_med']:.2f} = {ratio:.0%} of what this plant "
        f"delivered at {row['outdoor_c']:.1f} C in its own trailing history (alarm line {thr:.0%})"
        if pd.notna(ratio) and pd.notna(thr)
        else "no efficiency baseline yet (not enough history)"
    )
    evidence = [
        f"equipment.csv:{PLANT}:heat_output_kw,electric_kw:{utc_z(at - WINDOW)}..{utc_z(at)} ratio_to_baseline={ratio:.2f}"
        if pd.notna(ratio)
        else f"equipment.csv:{PLANT}:heat_output_kw,electric_kw:{utc_z(at - WINDOW)}..{utc_z(at)}",
        f"equipment.csv:{PLANT}:status:{utc_z(at - FAULT_WINDOW)}..{utc_z(at)} fault_samples={faults}",
        f"weather.csv:outdoor_c={row['outdoor_c']:.1f}@{utc_z(at)}",
    ]
    fc = ctx.forecast_min(at)
    if fc:
        evidence.append(
            f"weather_forecast.csv:issued {utc_z(fc[2])}: min {fc[0]:.1f} C at {utc_z(fc[1])}"
        )
    rooms = cold_rooms[:MAX_ROOMS_LISTED] if impact else []
    ep.flag_event = emit(
        ep,
        at,
        "flag_anomaly",
        [PLANT, *rooms],
        "critical" if impact else "high",
        f"Heat pump {ep.cause}: {eff}; {faults} fault sample(s) in the last 24 h. "
        + (
            f"{len(cold_rooms)} occupied rooms are already below the comfort band."
            if impact
            else "Occupied rooms are still within the comfort band."
        ),
        evidence=evidence,
        expected_outcome="Incident opened and tracked; comfort protection and service are organised in the linked decisions.",
        verification_plan="Efficiency ratio back above the alarm line for 6 h with no fault code; otherwise the incident stays open and escalates when occupied rooms fall below 20 C.",
        confidence=0.85 if (pd.notna(ratio) and faults) else 0.7,
    )

    tech, note = ctx.technician(at)
    common = dict(
        approval=True,
        evidence=evidence[:2] + [f"staff_roster.csv: {note}"],
        dependencies=[ep.flag_event],
        verification_plan="A work order for HP1 is created and completed; afterwards the efficiency ratio returns to its line and fault codes stop.",
        confidence=0.7,
    )
    if tech:
        emit(
            ep,
            at,
            "dispatch_technician",
            [PLANT, tech],
            "critical" if impact else "high",
            f"{note}: on-site diagnosis of {PLANT} ({ep.cause}). Persistent loss of efficiency with pressure faults "
            "typically needs a refrigeration contractor, so have the technician confirm and call one in.",
            resource_id=tech,
            expected_outcome="Technician confirms the fault and a contractor visit is booked. No repair effect is assumed.",
            **common,
        )
    else:
        emit(
            ep,
            at,
            "order_service",
            [PLANT],
            "critical" if impact else "high",
            f"Order an external HVAC contractor for {PLANT} ({ep.cause}); {note}. "
            + ("Occupied rooms are already cold: request the emergency slot. " if impact else "")
            + "Cost needs approval.",
            expected_outcome="Contractor visit booked at the earliest slot. No repair effect is assumed.",
            **common,
        )


def _plan_energy(ep, at, row, ctx, emit) -> None:
    fc = ctx.forecast_min(at)
    if fc is None:
        return
    fmin, fmin_at, issued = fc
    if ep.plan_forecast_min is not None and fmin > ep.plan_forecast_min - FORECAST_WORSE_K:
        return
    replanning = ep.plan_forecast_min is not None
    ep.plan_forecast_min = fmin
    demand = row["demand_a"] + row["demand_b"] * fmin if pd.notna(row["demand_a"]) else np.nan
    shortfall = pd.notna(demand) and ep.heat_cap_kw > 0 and demand > ep.heat_cap_kw
    capacity = (
        f"Estimated heat demand at the forecast minimum is ~{demand:.0f} kW against {ep.heat_cap_kw:.0f} kW, "
        f"the highest output seen since the incident began - {'a shortfall is likely' if shortfall else 'no shortfall expected yet'}. "
        if pd.notna(demand)
        else ""
    )
    plan = ctx.price_plan(at)
    evidence = [
        f"weather_forecast.csv:issued {utc_z(issued)}: min {fmin:.1f} C at {utc_z(fmin_at)}"
    ]
    price = ""
    if plan:
        evidence.append(
            f"tariff.csv:published<= {utc_z(at)}: peak {plan['peak']:.3f} EUR/kWh at {utc_z(plan['peak_at'])}, "
            f"cheapest before it {plan['cheap']:.3f} at {utc_z(plan['cheap_at'])}"
        )
        price = (
            f"Pre-heat around {utc_z(plan['cheap_at'])} ({plan['cheap']:.3f} EUR/kWh) rather than at the "
            f"{utc_z(plan['peak_at'])} peak ({plan['peak']:.3f}); cover peak-hour heat-pump load from the battery where charge allows. "
        )
    low, high = ctx.ds.comfort_band_c
    emit(
        ep,
        at,
        "optimize_energy",
        [PLANT, "BAT1"],
        "high" if shortfall else "medium",
        ("Forecast worsened - re-plan. " if replanning else "")
        + f"Comfort-first mode while {PLANT} is impaired: forecast minimum {fmin:.1f} C at {utc_z(fmin_at)}. {capacity}"
        f"{price}Occupied rooms are pre-heated only inside the {low:.0f}-{high:.0f} C band; no price-driven setback "
        "in occupied rooms until the plant is healthy - cost is not traded against comfort.",
        evidence=evidence,
        dependencies=[ep.flag_event],
        expected_outcome="Simulated: rooms enter the cold hours with stored heat and peak-price load is partly shifted. No measured saving is claimed.",
        verification_plan="Occupied-room temperatures stay >= 20 C through the forecast minimum; if rooms fall below the band the incident escalates.",
        confidence=0.6,
    )


def _preheat_arrivals(ep, at, occupied, ctx, emit) -> None:
    day = at.tz_convert(ctx.ds.local_tz).date()
    if day in ep.preheat_days or at not in occupied.index:
        return
    rooms = [
        r
        for r in ctx.arrivals(at, PREHEAT_HORIZON)
        if r in occupied.columns and not occupied.at[at, r]
    ]
    if not rooms:
        return
    ep.preheat_days.add(day)
    emit(
        ep,
        at,
        "adjust_room_setpoint",
        rooms,
        "medium",
        f"{len(rooms)} vacant rooms have arrivals booked within {PREHEAT_HORIZON / pd.Timedelta(hours=1):.0f} h. With {PLANT} impaired, "
        "leaving the 18 C setback takes longer than usual, so start heating them to 21 C now instead of at check-in.",
        evidence=[
            f"bookings.csv: confirmed arrivals {utc_z(at)}..{utc_z(at + PREHEAT_HORIZON)} known at decision time: {', '.join(rooms)}"
        ],
        dependencies=[ep.flag_event],
        expected_outcome="Simulated: arriving guests enter rooms that are already inside the comfort band.",
        verification_plan="Room temperature >= 20 C at the check-in event of each listed room.",
        confidence=0.6,
    )


def _escalate(ep, at, row, cold_rooms, normal, occupied, ctx, emit, *, service: bool) -> None:
    listed = cold_rooms[:MAX_ROOMS_LISTED]
    evidence = [
        f"room_telemetry.csv:{','.join(listed)}:temperature_c<20 with setpoint gap >= {SETPOINT_GAP_K:.0f} K for >= 1 h, windows closed @{utc_z(at)}",
        f"room_telemetry.csv: {len(cold_rooms)} occupied rooms cold vs trailing-14-day normal (95th pct) of {normal:.0f}",
        f"weather.csv:outdoor_c={row['outdoor_c']:.1f}@{utc_z(at)}",
    ]
    if service:
        emit(
            ep,
            at,
            "order_service",
            [PLANT, *listed],
            "critical",
            f"Escalation: {len(cold_rooms)} occupied rooms are now below the comfort band while {PLANT} is impaired "
            f"({ep.cause} since {utc_z(ep.start)}). Request an emergency contractor visit; approve the call-out cost.",
            approval=True,
            suffix="urgent",
            evidence=evidence,
            dependencies=[ep.flag_event],
            expected_outcome="Emergency visit confirmed. No repair effect is assumed until a completed work order appears.",
            verification_plan="Completed corrective work order for HP1, then cold-room count back to its normal level within 6 h.",
            confidence=0.8,
        )
    arriving = set(ctx.arrivals(at, SHED_HORIZON))
    shed = [r for r in occupied.columns if not occupied.at[at, r] and r not in arriving]
    if shed:
        emit(
            ep,
            at,
            "adjust_room_setpoint",
            shed,
            "high",
            f"Free heating capacity for occupied rooms: lower {len(shed)} vacant rooms without an arrival in the next "
            f"{SHED_HORIZON / pd.Timedelta(hours=1):.0f} h from the 18 C setback to {VACANT_SHED_SETPOINT_C:.0f} C (frost-safe) until the plant recovers.",
            suffix="shed",
            evidence=evidence[1:]
            + [
                f"bookings.csv: no confirmed arrival for these rooms before {utc_z(at + SHED_HORIZON)}"
            ],
            dependencies=[ep.flag_event],
            expected_outcome="Simulated: some heat is redirected to occupied rooms. The size of the effect is not measured.",
            verification_plan="Occupied-room temperatures stop falling; restore 18 C and pre-heat as soon as a room gets an arrival or the incident closes.",
            confidence=0.5,
        )


def _notify_proactive(ep, at, rooms, lang, deficits, emit) -> None:
    """One message per language group for rooms that just became clearly cold."""
    text, lang = _message("proactive", "{room}", lang)
    worst = float(deficits.max())
    emit(
        ep,
        at,
        "notify_guest",
        list(rooms),
        "high",
        f"{len(rooms)} occupied room(s) ({', '.join(rooms)}) have been up to {worst:.1f} K below the comfort band for "
        f"over an hour during an open {PLANT} incident; tell these guests before they have to ask.",
        suffix=lang,
        evidence=[
            f"room_telemetry.csv:{r}:temperature_c:{utc_z(at - pd.Timedelta(hours=1))}..{utc_z(at)}"
            for r in rooms
        ],
        dependencies=[ep.flag_event],
        expected_outcome="Message proposed for sending to each listed room ({room} = room number); delivery is not confirmed by this log.",
        verification_plan="No cold complaint from these rooms afterwards; rooms back >= 20 C after service. Offer a room move (needs approval) if one stays cold.",
        proposed_message=text,
        message_language=lang,
        confidence=0.8,
    )


def _reply(ep, at, request, emit) -> None:
    """Answer a cold complaint that arrived during an open incident, inside its deadline."""
    if request["request_id"] in ep.answered:
        return
    ep.answered.add(request["request_id"])
    room = request["room_id"]
    ep.notified.add((room, request["booking_id"]))  # no second, proactive message to this guest
    known = request["language"] if request["language"] in _MESSAGES["reply"] else None
    text, lang = _message("reply", room, known)
    # The incident was already open when the request came in, so the reply can go out on
    # the next 15-min tick instead of waiting for the hourly review.
    stamp = min(at, max(request["timestamp_utc"].ceil("15min"), ep.start))
    due = request["response_due_utc"]
    emit(
        ep,
        at,
        "notify_guest",
        [room],
        "high",
        f"Guest in {room} reported a cold room at {utc_z(request['timestamp_utc'])} during an open {PLANT} incident; "
        f"reply {'before ' + utc_z(due) if pd.notna(due) else 'promptly'} with the real cause and what we are doing.",
        stamp=stamp,
        suffix=request["request_id"],
        evidence=[f"guest_requests.csv:{request['request_id']}", f"decisions: incident {ep.id}"],
        guest_request_ids=[request["request_id"]],
        dependencies=[ep.flag_event],
        expected_outcome="Reply proposed for sending; delivery is not confirmed by this log.",
        verification_plan="No repeated complaint from this room; room back >= 20 C after service. Offer a room move (needs approval) if it stays cold.",
        proposed_message=text,
        message_language=lang,
        confidence=0.8,
    )


def _triage_complaint(ds, req, cold, below_band, occupied, handled: set) -> None:
    """A cold complaint with no plant incident open: explain it, and log why we stay quiet."""
    if req["request_id"] in handled:
        return
    handled.add(req["request_id"])
    at, room = req["timestamp_utc"], req["room_id"]
    evidence = [f"guest_requests.csv:{req['request_id']}"]
    if room not in cold.columns:
        return
    tel = ds.room_telemetry
    recent = tel[
        (tel["room_id"] == room) & tel["timestamp_utc"].between(at - pd.Timedelta(hours=2), at)
    ]
    last_checkin = ds.lifecycle[
        (ds.lifecycle["room_id"] == room)
        & ds.lifecycle["occupied"]
        & (ds.lifecycle["timestamp_utc"] <= at)
    ]["timestamp_utc"].max()
    temp = recent["temperature_c"].dropna()
    evidence.append(f"room_telemetry.csv:{room}:{utc_z(at - pd.Timedelta(hours=2))}..{utc_z(at)}")
    if pd.notna(last_checkin) and at - last_checkin < WARMUP_AFTER_CHECKIN:
        why = (
            f"guest checked in {(at - last_checkin) / pd.Timedelta(minutes=1):.0f} min earlier; the room is still "
            "warming up from the 18 C setback and the heat pump is healthy"
        )
    elif recent["window_open"].eq(1).any():
        why = "window was open in the last 2 h and the heat pump is healthy"
    elif temp.empty:
        why = "no valid temperature reading for the room, so the complaint cannot be confirmed from data"
    elif temp.iloc[-1] >= ds.comfort_band_c[0]:
        why = f"room measures {temp.iloc[-1]:.1f} C, inside the comfort band, and the heat pump is healthy"
    else:
        why = (
            f"room measures {temp.iloc[-1]:.1f} C but the heat pump is healthy and the building is otherwise "
            "normal - a single-room matter for guest service, not a plant incident"
        )
    audit.dismiss(
        ISSUE, room, at, f"Cold complaint not treated as a heating incident: {why}.", evidence
    )
