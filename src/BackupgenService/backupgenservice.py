from datetime import datetime
import helics as h
import random
import logging

from esdl import EnergySystem
from dots_infrastructure.DataClasses import TimeStepInformation, EsdlId
from dots_infrastructure.CalculationServiceHelperFunctions import get_single_param_with_name

from backupgen_service_base import BackupgenServiceBase
from backupgen_service_dataclasses import BackupStateOutput

LOGGER = logging.getLogger(__name__)

class BackupgenService(BackupgenServiceBase):

    def __init__(self):
        super().__init__()
        for calc in self.calculations:
            if calc.helics_value_federate_info.calculation_name == "backup_dispatch":
                calc.helics_value_federate_info.offset = 20

    def init_calculation_service(self, energy_system: EnergySystem):
        super().init_calculation_service(energy_system)
        LOGGER.info("Initializing Backup Generator Service...")
        self.generators = {}
        self._last_requested_power = {}  # esdl_id -> last requested power from Network Balancer
        
        for esdl_id in self.simulator_configuration.esdl_ids:
            capacity_w = 5_000_000.0
            startup_delay_s = 60.0
            
            esdl_gen = self.esdl_obj_mapping.get(esdl_id)
            if esdl_gen is not None:
                if getattr(esdl_gen, 'power', 0.0) > 0:
                    capacity_w = float(esdl_gen.power)
                
                if getattr(esdl_gen, 'KPIs', None) is not None:
                    for kpi in esdl_gen.KPIs.kpi:
                        if getattr(kpi, 'name', '') == 'startup_delay_s':
                            startup_delay_s = float(kpi.value)
                    
            self.generators[esdl_id] = {
                "status": "OFF",
                "capacity_w": capacity_w,
                "startup_delay_s": startup_delay_s
            }
            self._last_requested_power[esdl_id] = 0.0
            LOGGER.info(f"[ESDL] Generator {esdl_id}: {capacity_w/1e6:.1f}MW, delay={startup_delay_s}s")

    def backup_state(self, param_dict: dict, simulation_time: datetime, time_step_number: TimeStepInformation, esdl_id: EsdlId, energy_system: EnergySystem):
        """Publishes generator state using the last-received power request."""
        requested_power_w = self._last_requested_power.get(esdl_id, 0.0)

        state = self.generators.get(esdl_id)
        if not state:
            return BackupStateOutput(backup_supplied_power=0.0, available_max_power=0.0)

        capacity_w = state["capacity_w"]
        startup_delay_s = state["startup_delay_s"]
        
        time_step_s = float(getattr(self, "backup_state_period_seconds", 900.0))
        
        capped_request_w = min(max(0.0, requested_power_w), capacity_w)
        actual_power_w = 0.0
        
        if capped_request_w > 0:
            if state["status"] == "OFF":
                fraction_on = max(0.0, time_step_s - startup_delay_s) / time_step_s
                actual_power_w = capped_request_w * fraction_on
                state["status"] = "ON"
                LOGGER.info(f"Generator {esdl_id} SPINNING UP. fraction_on={fraction_on:.2f}")
            else:
                actual_power_w = capped_request_w

            actual_power_w *= random.uniform(0.99, 1.01)
            actual_power_w = max(0.0, min(actual_power_w, capacity_w))
        else:
            state["status"] = "OFF"
            actual_power_w = 0.0

        # Write internal states to InfluxDB for debugging/dashboards
        self.influx_connector.set_time_step_data_point(esdl_id, "status", simulation_time, state["status"])
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_supplied_power", simulation_time, actual_power_w)
        self.influx_connector.set_time_step_data_point(esdl_id, "available_max_power", simulation_time, capacity_w)

        return BackupStateOutput(
            backup_supplied_power=actual_power_w,
            available_max_power=capacity_w
        )

    def backup_dispatch(self, param_dict: dict, simulation_time: datetime, time_step_number: TimeStepInformation, esdl_id: EsdlId, energy_system: EnergySystem):
        """Consumes the backup power request from the Network Balancer and stores it."""
        requested_power_w = 0.0
        for k, v in param_dict.items():
            if "backup_requested_power" in k.lower():
                requested_power_w = float(v); break

        self._last_requested_power[esdl_id] = requested_power_w
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
