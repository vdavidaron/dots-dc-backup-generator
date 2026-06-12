from datetime import datetime
import helics as h
import logging
import requests

# Bound InfluxDB HTTP latency to prevent long-run accumulating-buffer hangs.
_orig_request = requests.Session.request
def _patched_request(self, method, url, **kwargs):
    if 'timeout' not in kwargs or kwargs['timeout'] is None:
        kwargs['timeout'] = 5.0
    return _orig_request(self, method, url, **kwargs)
requests.Session.request = _patched_request

from esdl import EnergySystem
from dots_infrastructure.DataClasses import TimeStepInformation, EsdlId
from dots_infrastructure.CalculationServiceHelperFunctions import get_single_param_with_name
from dots_infrastructure.Logger import LOGGER

from backupgen_service_base import BackupgenServiceBase
from backupgen_service_dataclasses import BackupStateOutput

class BackupgenService(BackupgenServiceBase):

    def __init__(self):
        super().__init__()

    def init_calculation_service(self, energy_system: EnergySystem):
        super().init_calculation_service(energy_system)
        LOGGER.info("Initializing Backup Generator Service...")
        self.generators = {}
        self._actual_supplied = {}   # esdl_id -> actual W supplied (computed in dispatch)

        # Scope-1 accounting factors, read from the ESDL ElectricityNetwork KPIs so
        # the backup agent reports the same diesel emission factor and fuel cost the
        # Network Balancer aggregates. Falls back to diesel defaults.
        self.backup_co2_factor = 600.0          # [gCO2/kWh]
        self.backup_cost_eur_per_kwh = 0.40     # [EUR/kWh]
        try:
            for obj in energy_system.eAllContents():
                ec = getattr(obj, "eClass", None)
                if ec is not None and ec.name == "ElectricityNetwork" and getattr(obj, "KPIs", None) is not None:
                    for kpi in obj.KPIs.kpi:
                        if getattr(kpi, "name", None) == "backup_co2_factor":
                            self.backup_co2_factor = float(kpi.value)
                        elif getattr(kpi, "name", None) == "backup_cost_eur_per_kwh":
                            self.backup_cost_eur_per_kwh = float(kpi.value)
        except Exception as exc:
            LOGGER.warning("[Backupgen] Could not read Scope-1 factors from ESDL: %s", exc)
        LOGGER.info(f"[Backupgen] Scope-1 factors: {self.backup_co2_factor:.0f} gCO2/kWh, "
                    f"{self.backup_cost_eur_per_kwh:.2f} EUR/kWh")

        for esdl_id in self.simulator_configuration.esdl_ids:
            capacity_w = 5_000_000.0
            
            esdl_gen = self.esdl_obj_mapping.get(esdl_id)
            if esdl_gen is not None:
                if getattr(esdl_gen, 'power', 0.0) > 0:
                    capacity_w = float(esdl_gen.power)
                    
            self.generators[esdl_id] = {
                "capacity_w": capacity_w,
            }
            self._actual_supplied[esdl_id] = 0.0
            LOGGER.info(f"[ESDL] Generator {esdl_id}: {capacity_w/1e6:.1f}MW")

    def backup_state(self, param_dict: dict, simulation_time: datetime, time_step_number: TimeStepInformation, esdl_id: EsdlId, energy_system: EnergySystem):
        """Publishes the last-computed supplied power and max capacity."""
        state = self.generators.get(esdl_id)
        if not state:
            return BackupStateOutput(backup_supplied_power=0.0, available_max_power=0.0)

        capacity_w = state["capacity_w"]
        actual_power_w = self._actual_supplied.get(esdl_id, 0.0)

        # Log to InfluxDB
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_supplied_power", simulation_time, actual_power_w)
        self.influx_connector.set_time_step_data_point(esdl_id, "available_max_power", simulation_time, capacity_w)

        return BackupStateOutput(
            backup_supplied_power=actual_power_w,
            available_max_power=capacity_w
        )

    def backup_dispatch(self, param_dict: dict, simulation_time: datetime, time_step_number: TimeStepInformation, esdl_id: EsdlId, energy_system: EnergySystem):
        """Consumes the backup power request and immediately computes supplied power."""
        requested_power_w = 0.0
        for k, v in param_dict.items():
            if "backup_requested_power" in k.lower():
                requested_power_w = float(v); break

        state = self.generators.get(esdl_id)
        if not state:
            self._actual_supplied[esdl_id] = 0.0
            return {}

        capacity_w = state["capacity_w"]
        actual_power_w = min(max(0.0, requested_power_w), capacity_w)

        self._actual_supplied[esdl_id] = actual_power_w

        self.influx_connector.set_time_step_data_point(esdl_id, "backup_requested_power_w", simulation_time, requested_power_w)
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_dispatched_power_w", simulation_time, actual_power_w)

        # Scope-1 footprint of this dispatch (on-site diesel combustion + fuel cost).
        dt_h = self.backup_dispatch_period_seconds / 3600.0
        energy_kwh = (actual_power_w / 1000.0) * dt_h
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_scope1_carbon_g", simulation_time, self.backup_co2_factor * energy_kwh)
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_fuel_cost_eur", simulation_time, self.backup_cost_eur_per_kwh * energy_kwh)

        # Periodic InfluxDB flush (once per simulated day).
        if not hasattr(self, "_steps_since_flush"):
            self._steps_since_flush = 0
        self._steps_since_flush += 1
        if self._steps_since_flush >= 96:
            try:
                if self.influx_connector.data_points:
                    self.influx_connector.write_output()
                    self.influx_connector.data_points.clear()
            except Exception as exc:
                LOGGER.warning("[Influx] Periodic flush failed: %s", exc)
            self._steps_since_flush = 0

        return {}

if __name__ == "__main__":
    executor = BackupgenService()
    try:
        executor.start_simulation()
    except Exception as e:
        LOGGER.error(f"Fatal: {e}")
        raise 
    finally:
        executor.stop_simulation()
