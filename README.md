# Backup Generator Calculation Service

A DOTS-helics calculation service that simulates backup generation (e.g., diesel or gas turbine generators) with capacity checks and dispatch controls.

## Table of Contents
- [Overview](#overview)
- [ESDL Asset Mapping](#esdl-asset-mapping)
- [Calculations & HELICS Federation](#calculations--helics-federation)
- [Data & InfluxDB Logging](#data--influxdb-logging)
- [Project Structure](#project-structure)
- [How to Build & Run](#how-to-build--run)

---

## Overview

The **Backup Generator Service** simulates a local dispatchable energy source that responds to backup requests from the Energy Management System (EMS) / Network Balancer. It models physical capacity limits and provides real-time power injection when the grid or local renewables are insufficient to meet the datacenter's demand.

The service logic is implemented in [backupgenservice.py](src/BackupgenService/backupgenservice.py) and inherits from the generated [BackupgenServiceBase](src/BackupgenService/backupgen_service_base.py) class.

---

## ESDL Asset Mapping

During simulation initialization (in `init_calculation_service`), the service parses the incoming Energy System Description Language (ESDL) topology to configure its generator properties:

- **ESDL Asset Type:** `GasProducer`
- **Properties Handled:**
  - `power`: Configures the generator's nameplate capacity in Watts. Defaults to `5_000_000.0` W (5 MW) if not defined or if set to `0.0`.
- **Dynamic Configuration:** Supports multiple `GasProducer` instances in a single simulation if multiple IDs are provided in the simulator configuration.

---

## Calculations & HELICS Federation

The service defines two core calculations configured in [input.json](input.json) and executed at a **15-minute (900 seconds)** cadence:

### 1. `backup_state`
- **Execution Interval:** 900 seconds
- **Offset:** 0 seconds
- **Purpose:** Publishes the generator's current availability and supplied power.
- **HELICS Outputs:**
  - `backup_supplied_power` (Unit: `W`, Type: `DOUBLE`): The actual electrical power supplied by this generator during the current step.
  - `available_max_power` (Unit: `W`, Type: `DOUBLE`): The maximum capacity of the generator.

### 2. `backup_dispatch`
- **Execution Interval:** 900 seconds
- **Offset:** 20 seconds (runs after the balancer calculates setpoints)
- **Purpose:** Consumes power requests from the balancer and updates the physical generator state.
- **HELICS Inputs:**
  - `backup_requested_power` (Unit: `W`, Type: `DOUBLE`, Published by `ElectricityNetwork`): The command from the network balancer specifying how much backup power is requested.
- **Logic:** Clamps the requested power between `0.0` and the generator's nameplate `capacity_w`. The resulting value is cached to be published by `backup_state` in the next timestep.

---

## Data & InfluxDB Logging

To prevent long-run accumulating buffer hangs, the service overrides the default InfluxDB write pacing. It buffers metrics in memory and performs a **periodic flush once every 96 steps (1 simulated day)**.

The following fields are written to the database under the `GasProducer` asset ID:
- `backup_supplied_power` (W): Actual power supplied to the grid/datacenter.
- `available_max_power` (W): Max capacity limit of the generator.
- `backup_requested_power_w` (W): Raw incoming demand request.
- `backup_dispatched_power_w` (W): Dispatched generator setpoint after capacity constraints are applied.

---

## Project Structure

- [pyproject.toml](pyproject.toml): Package configuration and dependency list (`helics==3.6.1`, `dots_infrastructure==1.0.9`).
- [Dockerfile](Dockerfile): Defines containerization using `python:3.13-slim`.
- [code_gen.py](code_gen.py): Code generator invocation script to rebuild base classes.
- [input.json](input.json): Federation calculation specifications.
- **src/BackupgenService/**
  - [backupgenservice.py](src/BackupgenService/backupgenservice.py): Primary logic overrides.
  - [backupgen_service_base.py](src/BackupgenService/backupgen_service_base.py): Base class handling HELICS boilerplate.
  - [backupgen_service_dataclasses.py](src/BackupgenService/backupgen_service_dataclasses.py): Return types for service calculations.

---

## How to Build & Run

### Local Execution (Python virtual environment)
Run the script directly to start the calculation service. It will block and wait for a HELICS broker to initiate the simulation:
```bash
python src/BackupgenService/backupgenservice.py
```

### Docker Build
Build the container image using the local context:
```bash
docker build -t backup-generator-service:latest .
```
The container entrypoint will automatically start the Python executor.

---

## Thesis modifications (MSc)

- **Scope-1 footprint telemetry.** The generator now reads `backup_co2_factor` (default 600 gCO2/kWh) and `backup_cost_eur_per_kwh` (default 0.40 EUR/kWh) from the `ElectricityNetwork` ESDL KPIs and logs `backup_scope1_carbon_g` and `backup_fuel_cost_eur` from its actual dispatched power, so the agent reports its own emissions and fuel cost. The authoritative aggregation into total carbon/cost is done by the Network Balancer (which reads the nameplate from the ESDL rather than subscribing to this federate).
