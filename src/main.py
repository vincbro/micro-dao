import argparse
import datetime
import json
import os
import time

from models import DigitalTwin, PriceSnapshot, ScheduleState
from optimizer import optimize_with_milp, static_optimizer

def main():
    TARGET_KG = 300.0
    ELECTROLYZER_CAPACITY_KW = 1000.0

    parser = argparse.ArgumentParser(description="Simulate and compare MILP vs Greedy EMS optimizers.")
    parser.add_argument("filepaths", nargs="+", help="One or more JSON files containing day-ahead price snapshots")
    parser.add_argument("--health", type=float, default=45.0, help="Starting stack health percentage [0.0 - 100.0] (default: 45.0)")
    args = parser.parse_args()
    
    filepaths = args.filepaths
    START_HEALTH = args.health
    
    # We now track THREE separate digital twins
    greedy_twin = DigitalTwin(START_HEALTH)
    milp_heur_on_twin = DigitalTwin(START_HEALTH)
    milp_heur_off_twin = DigitalTwin(START_HEALTH)
    
    # Trackers
    stats = {
        "greedy": {"opex": 0.0, "capex": 0.0, "starts": 0, "time_s": 0.0},
        "milp_on": {"opex": 0.0, "capex": 0.0, "starts": 0, "time_s": 0.0},
        "milp_off": {"opex": 0.0, "capex": 0.0, "starts": 0, "time_s": 0.0},
    }

    print(f"Starting Multi-Day Simulation ({len(filepaths)} days)...\n")

    for path in filepaths:
        filename = os.path.basename(path)
        
        snapshots: list[PriceSnapshot] = []
        with open(path) as file:
            data = json.load(file)
            # Fix: Demand perfectly matches the TARGET_KG
            constant_demand = TARGET_KG / len(data) 
            for row in data:
                start = datetime.datetime.fromisoformat(row["time_start"])
                end = datetime.datetime.fromisoformat(row["time_end"])
                snapshot = PriceSnapshot(
                    row["SEK_per_kWh"], row["EUR_per_kWh"], start, end, constant_demand
                )
                snapshots.append(snapshot)

        # ---------------------------------------------------------
        # 1. STANDARD EMS (Greedy)
        # ---------------------------------------------------------
        t0 = time.time()
        greedy_schedule = static_optimizer(
            snapshots, TARGET_KG, greedy_twin.current_efficiency_kwh_per_kg(), ELECTROLYZER_CAPACITY_KW
        )
        stats["greedy"]["time_s"] += (time.time() - t0)

        greedy_twin, daily_g_opex, daily_g_starts = calculate_cost(greedy_twin, greedy_schedule, ELECTROLYZER_CAPACITY_KW)
        daily_g_capex = daily_g_starts * greedy_twin.financial_cost_of_start()

        # ---------------------------------------------------------
        # 2. MILP (Heuristics ON - The Fast Guesser)
        # ---------------------------------------------------------
        
        t0 = time.time()
        milp_on_schedule = optimize_with_milp(
            snapshots, TARGET_KG, milp_heur_on_twin.financial_cost_of_start(), 
            milp_heur_on_twin.current_efficiency_kwh_per_kg(), ELECTROLYZER_CAPACITY_KW, 
            milp_heur_on_twin.current_storage_kg, milp_heur_on_twin.MAX_STORAGE_KG, 
            use_solver_heuristics=True
        )
        stats["milp_on"]["time_s"] += (time.time() - t0)

        milp_heur_on_twin, daily_on_opex, daily_on_starts = calculate_cost(milp_heur_on_twin, milp_on_schedule, ELECTROLYZER_CAPACITY_KW)
        daily_on_capex = daily_on_starts * milp_heur_on_twin.financial_cost_of_start()

        # ---------------------------------------------------------
        # 3. MILP (Heuristics OFF - Pure Math)
        # ---------------------------------------------------------
        
        t0 = time.time()
        milp_off_schedule = optimize_with_milp(
            snapshots, TARGET_KG, milp_heur_off_twin.financial_cost_of_start(), 
            milp_heur_off_twin.current_efficiency_kwh_per_kg(), ELECTROLYZER_CAPACITY_KW, 
            milp_heur_off_twin.current_storage_kg, milp_heur_off_twin.MAX_STORAGE_KG, 
            use_solver_heuristics=False
        )
        stats["milp_off"]["time_s"] += (time.time() - t0)

        milp_heur_off_twin, daily_off_opex, daily_off_starts = calculate_cost(milp_heur_off_twin, milp_off_schedule, ELECTROLYZER_CAPACITY_KW)
        daily_off_capex = daily_off_starts * milp_heur_off_twin.financial_cost_of_start()

        # Accumulate totals
        stats["greedy"]["opex"] += daily_g_opex
        stats["greedy"]["capex"] += daily_g_capex
        stats["greedy"]["starts"] += daily_g_starts
        
        stats["milp_on"]["opex"] += daily_on_opex
        stats["milp_on"]["capex"] += daily_on_capex
        stats["milp_on"]["starts"] += daily_on_starts

        stats["milp_off"]["opex"] += daily_off_opex
        stats["milp_off"]["capex"] += daily_off_capex
        stats["milp_off"]["starts"] += daily_off_starts

        # ---------------------------------------------------------
        # Daily Visual Printout
        # ---------------------------------------------------------
        print("-" * 65)
        print(f" DAY: {filename}")
        print("-" * 65)
        print_schedule_timeline(greedy_schedule, "Greedy EMS")
        print()
        print_schedule_timeline(milp_on_schedule, "MILP Solver ")
        print()

    # ---------------------------------------------------------
    # Final Printout (Updated to show OPEX/CAPEX breakdown)
    # ---------------------------------------------------------
    greedy_total = stats['greedy']['opex'] + stats['greedy']['capex']
    milp_total = stats['milp_on']['opex'] + stats['milp_on']['capex']
    savings = greedy_total - milp_total
    starts_avoided = stats['greedy']['starts'] - stats['milp_on']['starts']

    print("\n" + "="*65)
    print(" PERIOD SUMMARY & SOLVER BENCHMARK")
    print("="*65)
    print(f" Total Days Processed:    {len(filepaths)}")
    print(f" Total Avoided Cycles:    {starts_avoided} unnecessary starts")
    if savings > 0:
        print(f" NET FINANCIAL IMPACT:    {savings:.2f} SEK SAVED THIS PERIOD")
    else:
        print(f" NET FINANCIAL IMPACT:    {savings:.2f} SEK (Paid extra OPEX to save CAPEX)")
    print("-" * 65)
    
    print("\n [1] GREEDY EMS (Baseline)")
    print(f"     Starts:              {stats['greedy']['starts']}")
    print(f"     Total OPEX:          {stats['greedy']['opex']:.2f} SEK")
    print(f"     Total CAPEX (Wear):  {stats['greedy']['capex']:.2f} SEK")
    print(f"     Total Cost:          {greedy_total:.2f} SEK")
    print(f"     Solve Time:          {stats['greedy']['time_s']:.3f} s")
    
    print("\n [2] MILP (Heuristics ON)")
    print(f"     Starts:              {stats['milp_on']['starts']}")
    print(f"     Total OPEX:          {stats['milp_on']['opex']:.2f} SEK")
    print(f"     Total CAPEX (Wear):  {stats['milp_on']['capex']:.2f} SEK")
    print(f"     Total Cost:          {milp_total:.2f} SEK")
    print(f"     Solve Time:          {stats['milp_on']['time_s']:.3f} s")

    print("\n [3] MILP (Heuristics OFF)")
    print(f"     Starts:              {stats['milp_off']['starts']}")
    print(f"     Total OPEX:          {stats['milp_off']['opex']:.2f} SEK")
    print(f"     Total CAPEX (Wear):  {stats['milp_off']['capex']:.2f} SEK")
    print(f"     Total Cost:          {(stats['milp_off']['opex'] + stats['milp_off']['capex']):.2f} SEK")
    print(f"     Solve Time:          {stats['milp_off']['time_s']:.3f} s")
    print("="*65 + "\n")


def calculate_cost(twin: DigitalTwin, schedule: list[ScheduleState], capacity_kw: float) -> tuple[DigitalTwin, float, int]:
    twin_copy = DigitalTwin(twin.health * 100)
    opex_cost = 0.0
    starts = 0
    last_end_time = None
    was_on = False

    for state in schedule:
        if state.on:
            is_new_block = not was_on or (last_end_time and state.start > last_end_time)
            if is_new_block:
                twin_copy.apply_start_cycle()
                starts += 1
            duration_h = (state.end - state.start).total_seconds() / 3600.0
            energy_kwh = capacity_kw * duration_h
            opex_cost += energy_kwh * state.snapshot.kwh_price_sek
            last_end_time = state.end
            was_on = True
        else:
            was_on = False
            
    return twin_copy, opex_cost, starts


def print_schedule_timeline(schedule: list[ScheduleState], title: str):
    """Compresses the hour-by-hour schedule into continuous ON/OFF blocks."""
    print(f"     --- {title} Timeline ---")
    if not schedule:
        print("     No operations scheduled.")
        return

    current_state = schedule[0].on
    start_time = schedule[0].start
    last_time = schedule[0].end

    for state in schedule[1:]:
        if state.on == current_state:
            last_time = state.end
        else:
            status = " ON" if current_state else "OFF"
            print(f"     [{status}] {start_time.strftime('%H:%M')} -> {last_time.strftime('%H:%M')}")
            
            current_state = state.on
            start_time = state.start
            last_time = state.end

    status = " ON" if current_state else "OFF"
    print(f"     [{status}] {start_time.strftime('%H:%M')} -> {last_time.strftime('%H:%M')}")


if __name__ == "__main__":
    main()
