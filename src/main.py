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
        print("Usage: python main.py <file1.json> <file2.json> ...")
        os._exit(1)
        
    filepaths = args[1:]

    START_HEALTH = 45.0
    
    milp_twin = DigitalTwin(START_HEALTH)
    greedy_twin = DigitalTwin(START_HEALTH)
    
    weekly_milp_opex = 0.0
    weekly_milp_capex = 0.0
    weekly_milp_starts = 0
    
    weekly_greedy_opex = 0.0
    weekly_greedy_capex = 0.0
    weekly_greedy_starts = 0

    print(f"Starting Multi-Day Simulation ({len(filepaths)} days)...")

    for path in filepaths:
        filename = os.path.basename(path)
        
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

        milp_eff = milp_twin.current_efficiency_kwh_per_kg()
        milp_capex_pen = milp_twin.financial_cost_of_start()
        milp_schedule = optimize_with_milp(
            snapshots, TARGET_KG, milp_capex_pen, milp_eff, ELECTROLYZER_CAPACITY_KW
        )
        
        greedy_eff = greedy_twin.current_efficiency_kwh_per_kg()
        greedy_capex_pen = greedy_twin.financial_cost_of_start()
        greedy_schedule = static_optimizer(
            snapshots, TARGET_KG, greedy_eff, ELECTROLYZER_CAPACITY_KW
        )

        milp_twin, daily_milp_opex, daily_milp_starts = calculate_cost(
            milp_twin, milp_schedule, ELECTROLYZER_CAPACITY_KW
        )
        greedy_twin, daily_greedy_opex, daily_greedy_starts = calculate_cost(
            greedy_twin, greedy_schedule, ELECTROLYZER_CAPACITY_KW
        )

        # Calculate CAPEX cost based on the exact penalty for this specific day
        daily_milp_capex = daily_milp_starts * milp_capex_pen
        daily_greedy_capex = daily_greedy_starts * greedy_capex_pen

        # Accumulate totals
        weekly_milp_opex += daily_milp_opex
        weekly_milp_capex += daily_milp_capex
        weekly_milp_starts += daily_milp_starts

        weekly_greedy_opex += daily_greedy_opex
        weekly_greedy_capex += daily_greedy_capex
        weekly_greedy_starts += daily_greedy_starts

        daily_milp_total = daily_milp_opex + daily_milp_capex
        daily_greedy_total = daily_greedy_opex + daily_greedy_capex
        
        print("\n" + "="*65)
        print(f" DAILY BENCHMARK: {filename}")
        print("="*65)
        
        print(" [1] STANDARD EMS (Greedy Optimization)")
        print(f"     Start/Stop Cycles:   {daily_greedy_starts}")
        print(f"     Current Health:      {greedy_twin.health * 100:.4f}%")
        print(f"     Daily Cost (+Wear):  {daily_greedy_total:.2f} SEK")
        print_schedule_timeline(greedy_schedule, "Greedy EMS")
        print("-" * 65)
        
        print(" [2] micro-dao (MILP Optimization)")
        print(f"     Start/Stop Cycles:   {daily_milp_starts}")
        print(f"     Current Health:      {milp_twin.health * 100:.4f}%")
        print(f"     Daily Cost (+Wear):  {daily_milp_total:.2f} SEK")
        print_schedule_timeline(milp_schedule, "MILP DAO")

    weekly_milp_total = weekly_milp_opex + weekly_milp_capex
    weekly_greedy_total = weekly_greedy_opex + weekly_greedy_capex
    
    total_savings_sek = weekly_greedy_total - weekly_milp_total
    starts_avoided = weekly_greedy_starts - weekly_milp_starts
    health_saved_pct = (milp_twin.health - greedy_twin.health) * 100

    print("\n" + "="*65)
    print(" PERIOD SUMMARY")
    print("="*65)
    print(f" Total Days Processed:    {len(filepaths)}")
    print(f" Total Avoided Cycles:    {starts_avoided} unnecessary starts")
    print(f" Preserved Health:        +{health_saved_pct:.6f}% stack life saved")
    print("-" * 65)
    print(f" GREEDY TOTAL COST:       {weekly_greedy_total:.2f} SEK")
    print(f"   - Total OPEX:          {weekly_greedy_opex:.2f} SEK")
    print(f"   - Total CAPEX (Wear):  {weekly_greedy_capex:.2f} SEK")
    print("-" * 65)
    print(f" MILP TOTAL COST:         {weekly_milp_total:.2f} SEK")
    print(f"   - Total OPEX:          {weekly_milp_opex:.2f} SEK")
    print(f"   - Total CAPEX (Wear):  {weekly_milp_capex:.2f} SEK")
    print("="*65)
    if total_savings_sek > 0:
        print(f" NET FINANCIAL IMPACT:    {total_savings_sek:.2f} SEK SAVED THIS PERIOD")
    else:
        print(f" NET FINANCIAL IMPACT:    {total_savings_sek:.2f} SEK (Paid extra OPEX to save CAPEX)")
    print("="*65 + "\n")

def calculate_cost(twin: DigitalTwin, schedule: list[ScheduleState], capacity_kw: float) -> tuple[DigitalTwin, float, int]:
    # Creates a cloned twin so we don't accidentally mutate state during evaluation
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
