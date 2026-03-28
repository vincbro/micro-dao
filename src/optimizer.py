from datetime import timedelta
import pulp
from models import PriceSnapshot, ScheduleState

# Thresholds for treating a continuous fraction as effectively on/off.
_FRACTION_ON_THRESHOLD = 1e-4



def optimize_with_milp(
    snapshots: list[PriceSnapshot],
    target_kg: float,
    capex_penalty_sek: float,
    efficiency_kwh_per_kg: float,
    capacity_kw: float,
    solver_time_limit_s: int = 60,
) -> list[ScheduleState]:
    if not snapshots:
        raise ValueError("snapshots must not be empty.")
    if target_kg <= 0:
        raise ValueError(f"target_kg must be positive, got {target_kg}.")

    n = len(snapshots)
    indices = range(n)

    # Derived constants
    production_rate_kg_per_h = capacity_kw / efficiency_kwh_per_kg
    durations_h = [(s.end - s.start).total_seconds() / 3600.0 for s in snapshots]

    # Dynamic Break-Even Uptime Calculation
    avg_price_sek = sum(s.kwh_price_sek for s in snapshots) / n
    min_price_sek = min(s.kwh_price_sek for s in snapshots)
    price_spread = max(avg_price_sek - min_price_sek, 0.01)
    
    hourly_savings_sek = capacity_kw * price_spread
    dynamic_min_time_h = capex_penalty_sek / hourly_savings_sek
    dynamic_min_time_h = min(dynamic_min_time_h, 4.0)

    # Problem definition
    prob = pulp.LpProblem("Electrolyzer_Schedule", pulp.LpMinimize)

    x = pulp.LpVariable.dicts("load_factor", indices, lowBound=0, upBound=1, cat=pulp.LpContinuous)
    y = pulp.LpVariable.dicts("is_on", indices, cat=pulp.LpBinary)
    z = pulp.LpVariable.dicts("startup", indices, cat=pulp.LpBinary)

    # Objective: minimise energy cost + startup penalties
    prob += pulp.lpSum(
        snapshots[i].kwh_price_sek * capacity_kw * durations_h[i] * x[i]
        + capex_penalty_sek * z[i]
        for i in indices
    ), "Total_Cost"

    # Constraint: hit the hydrogen target exactly
    prob += (
        pulp.lpSum(x[i] * production_rate_kg_per_h * durations_h[i] for i in indices)
        == target_kg
    ), "Production_Target"

    # Constraints: link load factor to binary on/off
    for i in indices:
        prob += x[i] <= y[i], f"Fraction_UB_{i}"
        prob += x[i] >= y[i] * _FRACTION_ON_THRESHOLD, f"Fraction_LB_{i}"

    # Constraint: startup detection
    for i in indices:
        prob += z[i] <= y[i], f"Startup_UB_on_{i}"
        if i == 0:
            prob += z[i] >= y[i], f"Startup_LB_{i}"
        else:
            prob += z[i] >= y[i] - y[i - 1], f"Startup_LB_{i}"
            prob += z[i] <= 1 - y[i - 1], f"Startup_UB_prev_{i}"

    # Constraints: Dynamic Minimum Uptime & Downtime
    interval_starts = [s.start for s in snapshots]
    for i in range(n):
        for j in range(i + 1, n):
            time_diff_h = (interval_starts[j] - interval_starts[i]).total_seconds() / 3600.0
            
            if time_diff_h >= dynamic_min_time_h:
                break
                
            if i > 0:
                prob += y[i - 1] - y[i] <= 1 - y[j], f"MinDown_{i}_{j}"
            prob += y[j] >= z[i], f"MinUp_{i}_{j}"

    # Constraint: Close the "Ghost Running" Loophole
    # You must run at exactly 100% capacity if you claim to be continuously ON. 
    for i in range(n - 1):
        prob += x[i] >= y[i] + y[i + 1] - 1, f"FullLoad_Continuous_{i}"

    # Solve
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=solver_time_limit_s)
    status = prob.solve(solver)
    status_name = pulp.LpStatus[status]

    if status_name not in ("Optimal", "Not Solved"):
        raise RuntimeError(
            f"Solver returned non-optimal status: '{status_name}'. "
            "Consider relaxing constraints or checking input data."
        )

    schedule: list[ScheduleState] = []
    for i in indices:
        load = x[i].varValue or 0.0
        snap = snapshots[i]
        
        if load > _FRACTION_ON_THRESHOLD:
            active_seconds = load * (snap.end - snap.start).total_seconds()
            on_end_time = snap.start + timedelta(seconds=active_seconds)
            
            schedule.append(ScheduleState(True, snap.start, on_end_time, snap))
            
            if active_seconds < (snap.end - snap.start).total_seconds() - 1:
                schedule.append(ScheduleState(False, on_end_time, snap.end, snap))
        else:
            schedule.append(ScheduleState(False, snap.start, snap.end, snap))

    return schedule

def static_optimizer(
    snapshots: list[PriceSnapshot], 
    target_kg: float, 
    efficiency_kwh_per_kg: float,
    capacity_kw: float
) -> list[ScheduleState]:
    production_per_sec = capacity_kw / efficiency_kwh_per_kg / 3600.0
    active_sec_needed = target_kg / production_per_sec
    
    sorted_snapshots = sorted(snapshots, key=lambda s: s.kwh_price_sek)
    allocation_map = {id(snapshot): 0.0 for snapshot in snapshots}
    time_left = active_sec_needed
    
    for snapshot in sorted_snapshots:
        if time_left <= 0:
            break
            
        duration = (snapshot.end - snapshot.start).total_seconds()
        alloc_sec = min(duration, time_left)
        
        allocation_map[id(snapshot)] = alloc_sec
        time_left -= alloc_sec

    schedule: list[ScheduleState] = []
    
    for snapshot in snapshots:
        alloc_sec = allocation_map[id(snapshot)]
        
        if alloc_sec > 0:
            on_end_time = snapshot.start + timedelta(seconds=alloc_sec)
            schedule.append(ScheduleState(True, snapshot.start, on_end_time, snapshot))
            
            duration = (snapshot.end - snapshot.start).total_seconds()
            if alloc_sec < duration:
                schedule.append(ScheduleState(False, on_end_time, snapshot.end, snapshot))
        else:
            schedule.append(ScheduleState(False, snapshot.start, snapshot.end, snapshot))
            
    return schedule
