import sys
import os
import unittest
import helics as h
from datetime import datetime
from dots_infrastructure.DataClasses import SimulatorConfiguration
from esdl.esdl_handler import EnergySystemHandler
from dots_infrastructure import CalculationServiceHelperFunctions

current_dir = os.path.dirname(__file__)
src_path = os.path.abspath(os.path.join(current_dir, '..', 'src', 'BackupgenService'))
sys.path.append(src_path)

try:
    from backupgenservice import BackupgenService
except ImportError as e:
    print(f"Failed to import service: {e}")
    BackupgenService = None

BROKER_TEST_PORT = 23404
START_DATE_TIME = datetime(2024, 1, 1, 0, 0, 0)
SIMULATION_DURATION_IN_SECONDS = 960
TEST_ID = "test-id"

def mock_backupgen_environment():
    return SimulatorConfiguration(
        "BackupgenService",
        [TEST_ID],
        "Mock-Backupgen-Federate",
        "127.0.0.1",
        BROKER_TEST_PORT,
        "local-backupgen-test",
        SIMULATION_DURATION_IN_SECONDS,
        START_DATE_TIME,
        "localhost", "8086", "admin", "pass", "dots",
        h.HelicsLogLevel.DEBUG,
        ["GasProducer"]
    )

class TestBackupgenService(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_string = f"-f 1 --name=backupgenbroker --port={BROKER_TEST_PORT}"
        cls.broker = h.helicsCreateBroker("zmq", "", init_string)
        if not h.helicsBrokerIsConnected(cls.broker):
            raise RuntimeError("Could not start HELICS broker.")

    @classmethod
    def tearDownClass(cls):
        h.helicsBrokerDisconnect(cls.broker)
        h.helicsBrokerFree(cls.broker)
        h.helicsCloseLibrary()

    def setUp(self):
        CalculationServiceHelperFunctions.get_simulator_configuration_from_environment = mock_backupgen_environment
        
        self.esh = EnergySystemHandler()
        self.esh.load_file(os.path.join(current_dir, "test.esdl"))
        self.energy_system = self.esh.get_energy_system()

    def test_backupgen_initialization(self):
        if BackupgenService is None:
            self.fail("BackupgenService could not be imported. Check your imports!")
        
        try:
            service = BackupgenService()
            self.assertIsNotNone(service)
            print("\n[OK] BackupgenService successfully initialized!")
        except Exception as e:
            self.fail(f"BackupgenService crashed during setup: {e}")

    def test_backupgen_logic(self):
        service = BackupgenService()
        
        # Init states
        service.init_calculation_service(self.energy_system)
        
        # Craft inputs (time step = 900s, requested = 10000W)
        mock_params = {"Backup_requested_power": 10000.0} 
        
        class MockTimeStep:
            time_period_in_seconds = 900.0
        mock_time_step = MockTimeStep()
        
        # 1. First timestep: Generator was OFF, it should be starting, with delay 60s
        # fraction_on = (900-60)/900 = 840/900 = 0.93333
        # Expected average power = 10000 * 0.93333 = 9333.33 W \u00b1 1% capacity randomness (1% of 20,000 W = \u00b1200)
        output = service.real_time_backup(
            param_dict=mock_params,
            simulation_time=START_DATE_TIME,
            time_step_number=mock_time_step,
            esdl_id=TEST_ID,
            energy_system=self.energy_system
        )
        
        # Power should be bounded between 9333.33 - 200 and 9333.33 + 200
        self.assertTrue(9133.0 < output.backup_supplied_power < 9534.0, f"Power logic with startup delay failed! Got {output.backup_supplied_power}")
        self.assertEqual(output.available_max_power, 20000.0) # Assumes fallback capacity of 20000.0
        
        # 2. Second timestep: It is now functionally ON across the whole boundary
        output_step2 = service.real_time_backup(
            param_dict=mock_params,
            simulation_time=START_DATE_TIME,
            time_step_number=mock_time_step,
            esdl_id=TEST_ID,
            energy_system=self.energy_system
        )
        
        # Now supplying full requested 10000W \u00b1 200W noise
        self.assertTrue(9800.0 < output_step2.backup_supplied_power < 10200.0, f"Power logic in steady state ON failed! Got {output_step2.backup_supplied_power}")
        
        print("\n[OK] Backupgen math logic passed successfully!")

if __name__ == '__main__':
    unittest.main()
