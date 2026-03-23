import datetime
import json
import os
import sys


class PriceSnapshot:
    kwh_price_sek: float
    kwh_price_eur: float
    start: datetime.datetime
    end: datetime.datetime

    def __init__(
        self,
        kwh_price_sek: float,
        kwh_price_eur: float,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> None:
        self.kwh_price_sek = kwh_price_sek
        self.kwh_price_eur = kwh_price_eur
        self.start = start
        self.end = end


class ElectrolyzerState:
    on: bool
    start: datetime.datetime
    end: datetime.datetime
    price_snapshot: PriceSnapshot

    def __init__(
        self,
        on: bool,
        start: datetime.datetime,
        end: datetime.datetime,
        snapshot: PriceSnapshot,
    ) -> None:
        self.on = on
        self.start = start
        self.end = end
        self.price_snapshot = snapshot


def main():
    TARGET_KG = 300.0
    ELECTROLYZER_CAPACITY_KW = 1000.0
    EFFICIENCY_KWH_PER_KG = 50.0
    PRODUCTION_PER_SEC = ELECTROLYZER_CAPACITY_KW / EFFICIENCY_KWH_PER_KG / 3600
    ACTIVE_SEC = TARGET_KG / PRODUCTION_PER_SEC

    args = sys.argv
    if len(args) < 2:
        print("Missing price data")
        os._exit(1)
    path = args[1]
    print(f"Opening and parsing {path}")
    price_snapshots: list[PriceSnapshot] = []
    with open(path) as file:
        data = json.load(file)
        for row in data:
            start = datetime.datetime.fromisoformat(row["time_start"])
            end = datetime.datetime.fromisoformat(row["time_end"])
            price_sek = row["SEK_per_kWh"]
            price_eur = row["EUR_per_kWh"]
            snapshot = PriceSnapshot(price_sek, price_eur, start, end)
            price_snapshots.append(snapshot)

    price_snapshots.sort(key=lambda x: x.kwh_price_sek)

    active_states: list[ElectrolyzerState] = []
    time_left = ACTIVE_SEC
    cost = 0
    for snapshot in price_snapshots:
        if time_left <= 0:
            break
        duration = (snapshot.end - snapshot.start).total_seconds()
        machine_cost_per_sec = (
            snapshot.kwh_price_sek * ELECTROLYZER_CAPACITY_KW
        ) / 3600.0
        if duration > time_left:
            cost += machine_cost_per_sec * time_left
            snapshot.end = snapshot.start + datetime.timedelta(0, time_left)
            state = ElectrolyzerState(
                True,
                snapshot.start,
                snapshot.start + datetime.timedelta(0, time_left),
                snapshot,
            )
            active_states.append(state)
            time_left = 0
        else:
            cost += machine_cost_per_sec * duration
            time_left -= duration
            state = ElectrolyzerState(True, snapshot.start, snapshot.end, snapshot)
            active_states.append(state)

    price_snapshots.sort(key=lambda x: x.start.timestamp())
    active_states.sort(key=lambda x: x.start.timestamp())

    active_state_map = {id(state.price_snapshot): state for state in active_states}
    states: list[ElectrolyzerState] = []

    for snap in price_snapshots:
        if id(snap) in active_state_map:
            active_state = active_state_map[id(snap)]
            states.append(active_state)

            if active_state.end < snap.end:
                states.append(ElectrolyzerState(False, active_state.end, snap.end, snap))
        else:
            states.append(ElectrolyzerState(False, snap.start, snap.end, snap))
    print(f"\n{'START':<20} {'END':<20} {'STATE':<10} {'PRICE (SEK/kWh)':<15}")
    print("-" * 70)

    for state in states:
        status_color = "O ON" if state.on else "X OFF"

        print(
            f"{state.start.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"{state.end.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"{status_color:<10} "
            f"{state.price_snapshot.kwh_price_sek:.4f}kr"
        )
    print(f"Total cost: {cost}")


if __name__ == "__main__":
    main()
