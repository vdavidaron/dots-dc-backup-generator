from datetime import datetime
import helics as h
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

    def init_calculation_service(self, energy_system: EnergySystem):
        super().init_calculation_service(energy_system)
        LOGGER.info("Initializing Backup Generator Service...")
        self.generators = {}
        self._actual_supplied = {}   # esdl_id -> actual W supplied (computed in dispatch)
        
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
        
        status = "ON" if actual_power_w > 0 else "OFF"
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_dispatch_status", simulation_time, status)
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_requested_power_w", simulation_time, requested_power_w)
        self.influx_connector.set_time_step_data_point(esdl_id, "backup_dispatched_power_w", simulation_time, actual_power_w)

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
