import datetime
import json
import os
import sys

from models import DigitalTwin, ScheduleState, PriceSnapshot
from optimizer import optimize_with_milp, static_optimizer

def main():
    TARGET_KG = 300.0
    ELECTROLYZER_CAPACITY_KW = 1000.0

    args = sys.argv
    if len(args) < 2:
        print("Missing price data")
        os._exit(1)
        
    path = args[1]
    print(f"Opening and parsing {path}")
    
    snapshots: list[PriceSnapshot] = []
    with open(path) as file:
        data = json.load(file)
        for row in data:
            start = datetime.datetime.fromisoformat(row["time_start"])
            end = datetime.datetime.fromisoformat(row["time_end"])
            price_sek = row["SEK_per_kWh"]
            price_eur = row["EUR_per_kWh"]
            snapshot = PriceSnapshot(price_sek, price_eur, start, end)
            snapshots.append(snapshot)

    base_twin = DigitalTwin(45.0)
    current_eff = base_twin.current_efficiency_kwh_per_kg()
    capex_penalty = base_twin.financial_cost_of_start()

    print("Running MILP Optimizer...")
    milp_schedule = optimize_with_milp(
        snapshots, TARGET_KG, capex_penalty, current_eff, ELECTROLYZER_CAPACITY_KW
    )
    
    print("Running Greedy EMS Optimizer...")
    greedy_schedule = static_optimizer(
        snapshots, TARGET_KG, current_eff, ELECTROLYZER_CAPACITY_KW
    )

    milp_twin, milp_opex, milp_starts = calculate_cost(
        base_twin, milp_schedule, ELECTROLYZER_CAPACITY_KW
    )
    greedy_twin, greedy_opex, greedy_starts = calculate_cost(
        base_twin, greedy_schedule, ELECTROLYZER_CAPACITY_KW
    )

    starts_avoided = greedy_starts - milp_starts
    health_saved_pct = (milp_twin.health - greedy_twin.health) * 100
    
    milp_total_cost = milp_opex + (milp_starts * capex_penalty)
    greedy_total_cost = greedy_opex + (greedy_starts * capex_penalty)
    
    total_savings_sek = greedy_total_cost - milp_total_cost

    print("\n" + "="*65)
    print(" SYSTEM BENCHMARK: STANDARD EMS vs DEGRADATION-AWARE MILP")
    print("="*65)
    print(f" Target Production:       {TARGET_KG:.1f} kg H2")
    print(f" Electrolyzer Capacity:   {ELECTROLYZER_CAPACITY_KW:.0f} kW")
    print("-" * 65)
    
    print(" [1] STANDARD EMS (Greedy Optimization)")
    print(f"     Start/Stop Cycles:   {greedy_starts}")
    print(f"     Final Stack Health:  {greedy_twin.health * 100:.4f}%")
    print(f"     Realized OPEX:       {greedy_opex:.2f} SEK")
    print(f"     True Cost (+Wear):   {greedy_total_cost:.2f} SEK")
    print("")
    print_schedule_timeline(greedy_schedule, "Greedy EMS")
    print("-" * 65)
    
    print(" [2] micro-dao (MILP Optimization)")
    print(f"     Start/Stop Cycles:   {milp_starts}")
    print(f"     Final Stack Health:  {milp_twin.health * 100:.4f}%")
    print(f"     Realized OPEX:       {milp_opex:.2f} SEK")
    print(f"     True Cost (+Wear):   {milp_total_cost:.2f} SEK")
    print("")
    print_schedule_timeline(milp_schedule, "MILP DAO")
    print("="*65)
    
    print(" THE DAO ADVANTAGE (Daily Impact)")
    print(f"     Avoided Wear Cycles: {starts_avoided} unnecessary starts")
    print(f"     Preserved Health:    +{health_saved_pct:.6f}% stack life saved")
    if total_savings_sek > 0:
        print(f"     Total Cost Savings:  {total_savings_sek:.2f} SEK saved today")
    else:
        print(f"     Total Cost Impact:   {total_savings_sek:.2f} SEK (Paid extra OPEX to save CAPEX)")
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
