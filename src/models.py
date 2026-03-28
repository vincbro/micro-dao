from datetime import datetime

class PriceSnapshot:
    def __init__(
        self,
        kwh_price_sek: float,
        kwh_price_eur: float,
        start: datetime,
        end: datetime,
    ) -> None:
        self.kwh_price_sek = kwh_price_sek
        self.kwh_price_eur = kwh_price_eur
        self.start = start
        self.end = end


class ScheduleState:
    def __init__(
        self,
        on: bool,
        start: datetime,
        end: datetime,
        snapshot: PriceSnapshot,
    ) -> None:
        self.on = on
        self.start = start
        self.end = end
        self.snapshot = snapshot

class DigitalTwin:
    def __init__(self, init_health_prec: float) -> None:
        self.health = init_health_prec / 100.0

        # Physics Constants
        self.BASE_EFFICIENCY = 50.0  # kWh required per kg when new
        self.MAX_DEGRADATION_PENALTY = 0.20  # 20% worse efficiency at end of life
        
        self.MAX_LIFETIME_CYCLES = 5000.0  # How many starts it survives
        self.BASE_WEAR_PER_CYCLE = 1.0 / self.MAX_LIFETIME_CYCLES
        self.STRESS_EXPONENT = 1.5  # Makes older stacks more fragile        
        self.REPLACEMENT_COST = 2_000_000 # The cost to replace a stack
      
    def current_efficiency_kwh_per_kg(self) -> float:
        # E_req(H) = E_base * (1 + delta_max * (1 - H))
        penalty_factor = self.MAX_DEGRADATION_PENALTY * (1.0 - self.health)
        return self.BASE_EFFICIENCY * (1.0 + penalty_factor)

    def damage_from_start_cycle(self) -> float:
        # Prevent division by zero if health is completely 0
        safe_health = max(self.health, 0.01)
        
        # W_cycle(H) = W_base * (1 / H)^k
        wear = self.BASE_WEAR_PER_CYCLE * ((1.0 / safe_health) ** self.STRESS_EXPONENT)
        return wear

    def apply_start_cycle(self) -> None:
        damage = self.damage_from_start_cycle()
        self.health = max(0.0, self.health - damage)

    def financial_cost_of_start(self) -> float:
        damage_pct = self.damage_from_start_cycle()
        return self.REPLACEMENT_COST * damage_pct
