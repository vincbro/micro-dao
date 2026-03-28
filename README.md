	
# micro-dao: Intelligent Electrolyzer Scheduling

A lightweight, high-performance Python simulation of an intelligent optimization layer for electrolyzer operations. This tool schedules hydrogen production to minimize the true Levelized Cost of Hydrogen (LCOH) by balancing day-ahead electricity spot prices against the CAPEX cost of stack degradation.

## The Problem: The Standard EMS Flaw
Existing energy management systems were designed for power plants and grid applications. They optimize for one variable: energy cost. For hydrogen production, this is incomplete. 

Dynamic operation accelerates cell degradation. Every ramp, load change, and start-stop cycle wears down electrolyzer stacks. Standard EMS platforms optimize energy cost without accounting for degradation costs per cycle. A standard EMS might shut down an electrolyzer to dodge a minor electricity price spike, unwittingly causing severe physical stack damage. 

## The Solution: Degradation-Aware Operation (DAO)
`micro-dao` implements a physics-aware scheduling engine based on the concept of Degradation Aware Operation. It produces production schedules that minimize total hydrogen cost, not just today's electricity bill, but the full cost including stack degradation. 

Every ramp has a CAPEX cost. DAO quantifies it and includes it in every scheduling decision.

### Key Features
* **Sub-Hour Precision:** Spot prices are hourly, but real-world control systems operate in seconds. `micro-dao` allocates production time down to the exact second, allowing it to cut off production mid-hour once the hydrogen target is perfectly met.
* **Gap-Filling Heuristic:** The algorithm evaluates "OFF" gaps in the schedule. If the cost of running through an expensive price spike is lower than the CAPEX wear penalty of a start/stop cycle, it bridges the gap, prioritizing stack lifetime extension.
* **Resolution & Horizon Agnostic:** The core optimization engine is completely decoupled from fixed time boundaries. It does not assume 24-hour windows or 60-minute intervals. Whether fed a 15-minute high-frequency trading window or a multi-year historical dataset, the mathematics and degradation physics scale natively.
* **Digital Twin Foundations:** The architecture is prepared for a physics-based model where stack health impacts both electrical efficiency and cold-start energy requirements over time.

## Algorithm Runtime Flow
To solve the degradation routing problem without importing heavy, slow mathematical solvers, `micro-dao` utilizes a lightning-fast, multi-pass pipeline to balance OPEX (electricity) against CAPEX (stack degradation).

**Phase 1: The Energy Baseline (OPEX Minimization)**
1. The system ingests the electricity price data.
2. It sorts these snapshots from cheapest to most expensive.
3. It allocates the required production time strictly to the cheapest available periods, clipping the final period to the exact second. 

**Phase 2: Timeline Reconstruction**
1. The allocated "ON" periods are re-sorted back into a chronological timeline. 
2. This reveals the physical reality of a standard EMS schedule: highly fragmented operations with multiple "OFF" gaps as the system blindly dodges minor price spikes.

**Phase 3: Degradation Aware Optimization (CAPEX vs OPEX)**
1. The algorithm runs a second, chronological optimization pass over the timeline.
2. For every "OFF" gap, it calculates a cost-minimization function: *Is the extra OPEX (electricity cost) of running straight through this gap cheaper than the CAPEX wear penalty of a cold shutdown and restart?*
3. If running is cheaper than restarting, the algorithm "bridges the gap," overriding the standard EMS baseline. 
4. The system dynamically scales this CAPEX penalty based on the `DigitalTwin`'s current Stack Health, becoming more conservative (preferring baseload operation) as the stack ages.

## How to Run (One-Command Setup)
Developer empathy is critical. You do not need to fight with virtual environments or version conflicts to evaluate this code. This project uses `uv` (an extremely fast Python package manager written in Rust) to handle dependencies instantly.

```bash
uv run main.py
```
