from datetime import timedelta
import pulp
from models import PriceSnapshot, ScheduleState

import logging
import pulp
from dataclasses import dataclass
from datetime import timedelta

logger = logging.getLogger(__name__)

# Thresholds for treating a continuous fraction as effectively on/off.
_FRACTION_ON_THRESHOLD = 1e-4
_FRACTION_FULL_THRESHOLD = 1 - 1e-4


import logging
import pulp
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds for treating a continuous fraction as effectively on/off.
_FRACTION_ON_THRESHOLD = 1e-4


def optimize_with_milp(
    snapshots: list[PriceSnapshot],
    target_kg: float,
    capex_penalty_sek: float,
    efficiency_kwh_per_kg: float,
    capacity_kw: float,
    min_down_time_h: float = 1.0,
    solver_time_limit_s: int = 60,
) -> list[ScheduleState]:
    """
    Optimises hydrogen production schedule using Mixed-Integer Linear Programming.

    The electrolyzer may run at any fraction [0, 1] of its capacity within each
    price interval.  Binary variable y[i] indicates whether it is on at all;
    z[i] fires whenever the unit transitions from off to on (startup event).

    The objective minimises total energy cost plus startup (CAPEX) penalties,
    subject to delivering exactly `target_kg` of hydrogen.

    Args:
        snapshots:           Ordered, non-overlapping price intervals.
        target_kg:           Exact hydrogen production target [kg].
        capex_penalty_sek:   One-off cost charged per startup event [SEK].
        efficiency_kwh_per_kg: Energy required per kg of hydrogen [kWh/kg].
        capacity_kw:         Nameplate electrical capacity [kW].
        min_down_time_h:     Minimum continuous off-time after a shutdown [h].
        solver_time_limit_s: Hard wall-clock limit for the CBC solver [s].

    Returns:
        A flat list of ScheduleState segments covering every snapshot interval.

    Raises:
        ValueError: If inputs are degenerate (empty snapshots, non-positive target).
        RuntimeError: If the solver reports the problem as infeasible or unbounded.
    """
    if not snapshots:
        raise ValueError("snapshots must not be empty.")
    if target_kg <= 0:
        raise ValueError(f"target_kg must be positive, got {target_kg}.")

    n = len(snapshots)
    indices = range(n)

    # Derived constants
    production_rate_kg_per_h = capacity_kw / efficiency_kwh_per_kg  # kg/h at full load
    durations_h = [(s.end - s.start).total_seconds() / 3600.0 for s in snapshots]

    # ------------------------------------------------------------------
    # Problem definition
    # ------------------------------------------------------------------
    prob = pulp.LpProblem("Electrolyzer_Schedule", pulp.LpMinimize)

    # x[i] ∈ [0, 1]: load factor during interval i (fraction of nameplate capacity).
    # Running at 50 % load for a full hour is physically identical to running at
    # 100 % load for half an hour under constant efficiency, but this formulation
    # avoids splitting intervals into ON + OFF sub-segments in the output, which
    # would create artificial OFF tails that bypass the minimum-downtime constraint.
    x = pulp.LpVariable.dicts("load_factor", indices, lowBound=0, upBound=1, cat=pulp.LpContinuous)
    # y[i] ∈ {0, 1}: whether the unit is on at all during interval i
    y = pulp.LpVariable.dicts("is_on", indices, cat=pulp.LpBinary)
    # z[i] ∈ {0, 1}: startup indicator (off → on transition before interval i)
    z = pulp.LpVariable.dicts("startup", indices, cat=pulp.LpBinary)

    # ------------------------------------------------------------------
    # Objective: minimise energy cost + startup penalties
    # ------------------------------------------------------------------
    prob += pulp.lpSum(
        snapshots[i].kwh_price_sek * capacity_kw * durations_h[i] * x[i]
        + capex_penalty_sek * z[i]
        for i in indices
    ), "Total_Cost"

    # ------------------------------------------------------------------
    # Constraint 1: hit the hydrogen target exactly
    # ------------------------------------------------------------------
    prob += (
        pulp.lpSum(x[i] * production_rate_kg_per_h * durations_h[i] for i in indices)
        == target_kg
    ), "Production_Target"

    # ------------------------------------------------------------------
    # Constraints 2 & 3: link load factor to binary on/off
    # ------------------------------------------------------------------
    for i in indices:
        prob += x[i] <= y[i], f"Fraction_UB_{i}"   # load only when on
        prob += x[i] >= y[i] * _FRACTION_ON_THRESHOLD, f"Fraction_LB_{i}"

    # ------------------------------------------------------------------
    # Constraint 4: startup detection  z[i] = max(0, y[i] - y[i-1])
    #   Tight formulation: z[i] >= y[i] - y[i-1]   (fires on 0→1 transition)
    #                      z[i] <= y[i]              (can't start when off)
    #                      z[i] <= 1 - y[i-1]        (no start if already on)
    # The solver drives z to 0 in minimisation, so only lower bounds are
    # strictly necessary, but the upper bounds tighten the LP relaxation and
    # speed up branch-and-bound.
    # ------------------------------------------------------------------
    for i in indices:
        prob += z[i] <= y[i], f"Startup_UB_on_{i}"
        if i == 0:
            prob += z[i] >= y[i], f"Startup_LB_{i}"
        else:
            prob += z[i] >= y[i] - y[i - 1], f"Startup_LB_{i}"
            prob += z[i] <= 1 - y[i - 1], f"Startup_UB_prev_{i}"

    # ------------------------------------------------------------------
    # Constraint 5: minimum shutdown duration
    # If the unit transitions ON→OFF at the start of interval i
    # (i.e. y[i-1]=1, y[i]=0), it must remain off for all intervals j
    # whose start falls within the min_down_time_h window.
    #
    # Formulation: y[i-1] - y[i] <= 1 - y[j]
    # Equivalent to: y[j] <= 1 - (y[i-1] - y[i])
    # When no shutdown occurred the LHS ≤ 0, so the constraint is slack.
    # ------------------------------------------------------------------
    shutdown_start_times = [s.start for s in snapshots]
    for i in range(1, n):
        for j in range(i + 1, n):
            time_since_shutdown_h = (
                shutdown_start_times[j] - shutdown_start_times[i]
            ).total_seconds() / 3600.0

            if time_since_shutdown_h >= min_down_time_h:
                break  # intervals are ordered; no later j can be in the window

            prob += y[i - 1] - y[i] <= 1 - y[j], f"MinDown_{i}_{j}"

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=solver_time_limit_s)
    status = prob.solve(solver)
    status_name = pulp.LpStatus[status]

    if status_name not in ("Optimal", "Not Solved"):
        raise RuntimeError(
            f"Solver returned non-optimal status: '{status_name}'. "
            "Consider relaxing constraints or checking input data."
        )
    if status_name == "Not Solved":
        # CBC hit the time limit — solution may still be usable (best incumbent).
        logger.warning(
            "Solver hit the time limit (%ds) without proving optimality. "
            "Using best incumbent solution.",
            solver_time_limit_s,
        )

    objective_value = pulp.value(prob.objective)
    logger.debug("Solved '%s': status=%s, objective=%.2f SEK", prob.name, status_name, objective_value)

    # ------------------------------------------------------------------
    # Extract schedule
    # Each snapshot interval maps to exactly ONE ScheduleState — either ON
    # (possibly at partial load) or OFF.  There are no intra-interval splits,
    # so the minimum-downtime guarantee carries through to the output 1-to-1.
    # ------------------------------------------------------------------
    schedule: list[ScheduleState] = []
    for i in indices:
        load = x[i].varValue or 0.0
        snap = snapshots[i]
        is_on = load > _FRACTION_ON_THRESHOLD
        schedule.append(ScheduleState(is_on, snap.start, snap.end, snap))

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
