import time

from .snmp import SNMPManager
from .const import _LOGGER


class RaritanPDUOutlet:
    def __init__(self, snmp_manager: SNMPManager, index: int, energy_support: bool):
        self.snmp_manager: SNMPManager = snmp_manager
        self.index = index
        self.energy_support = energy_support

        # Ignore some data for performance optimization
        self.sensor_data = {
            "label": "",

            # A value for each outlet which describes the operational state of the outlet. It is also used to set the operational state of the outlet Enumeration: 'on': 1, 'cycling': 2, 'off': 0, 'error': -1.
            # "operational_state": 0,

            # A unique value for the current sensor attached to the outlet. This value is reported in milliamps (1/1000th of an amp)
            "current": 0,

            # A unique value for the max. current sensor attached to the outlet. This value is reported in milliamps (1/1000th of an amp)
            # "max_current": 0,

            # A unique value for the voltage sensor attached to the outlet.This value is reported in millivolts (1/1000th of a volt)
            "voltage": 0,

            # A unique value for the active power sensor attached to the outlet. This value is reported in Watts. The real power consumption.
            "active_power": 0,

            # A unique value for the apparent power sensor attached to the outlet. This value is reported in Volt-Amps. This is the product of current and voltage.
            # "apparent_power": 0,

            # A unique value for the power factor of the outlet. The reading represents a percentage in the range of 0% to 100%. The power factor, a ratio of real power to apparent power.
            "power_factor": 0,

            # The value of the upper warning (non-critical) current threshold for the outlet. This value is reported in milliamps (1/1000th of an amp)
            # "current_upper_warning": 0,

            # The value of the upper critical current threshold for the outlet. This value is reported in milliamps (1/1000th of an amp)
            # "current_upper_critical": 0,

            # The value of the lower warning (non-critical) current threshold for the outlet. This value is reported in milliamps (1/1000th of an amp)
            # "current_lower_warning": 0,

            # The value of the lower critical current threshold for the outlet. This value is reported in milliamps (1/1000th of an amp)
            # "current_lower_critical": 0,

            # The hysteresis used for deassertions. This value is reported in milliamps (1/1000th of an amp)
            # "current_hysteresis": 0,

            # The current rating of the outlet. This value is reported in milliamps (1/1000th of an amp). The rated maximum current that the system can safely handle, in milliamps
            # "current_rating": 0,

            # NOT SUPPORTED by all PDUs. The value of the cumulative active energy for this outlet. This value is reported in WattHours. The total energy consumption in watt-hours (accumulated over time)
            # "watt_hours": 0,
        }
        self.last_sensor_data_update_timestamp = 0

        self.initial_energy_delivered = 0  # energy delivered from previous sessions
        self.energy_delivered = 0  # energy delivered in current session

        if energy_support:
            self.sensor_data["watt_hours"] = 0

    def initialize_energy_delivered(self, initial_value: float):
        self.initial_energy_delivered = initial_value
        _LOGGER.debug(f"Initialize Outlet {self.index} initial_energy_delivered to {self.initial_energy_delivered}")

    def update_energy_delivered(self, current_sensor_data_update_timestamp):
        # not enough data to estimate
        if self.last_sensor_data_update_timestamp == 0:
            return  # abort

        time_diff_seconds = current_sensor_data_update_timestamp - self.last_sensor_data_update_timestamp
        if time_diff_seconds < 0:
            return  # abort

        time_diff_hours = time_diff_seconds / (60.0 * 60.0)
        new_energy_delivered = self.sensor_data["active_power"] * time_diff_hours
        self.energy_delivered += new_energy_delivered

    def update_last_sensor_data_update_timestamp(self, current_sensor_data_update_timestamp):
        self.last_sensor_data_update_timestamp = current_sensor_data_update_timestamp

    def get_data(self):
        data = self.sensor_data.copy()
        _LOGGER.debug(f"Retried sensor data from Outlet {self.index} {str(data)}")

        data["energy_delivered"] = self.energy_delivered + self.initial_energy_delivered
        _LOGGER.debug(
            f"Retried energy data from Outlet energy_delivered: {self.energy_delivered} initial_energy_delivered:{self.initial_energy_delivered}")

        return data


class RaritanPDU:
    def __init__(self, host: str, port: int, community: str) -> None:
        """Initialize."""
        self.unique_id = f"{host}:{port} {community}"
        self.snmp_manager: SNMPManager = SNMPManager(host, port, community)
        self.name = ""
        self.energy_support = False
        self.outlet_count = 0
        self.cpu_temperature = 0
        self.outlets: [RaritanPDUOutlet] = []

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        try:
            result = await self.snmp_manager.snmp_get(["SNMPv2-MIB", "sysDescr", 0])
            if result is None:
                return False
            return str(result).startswith("Raritan Dominion PX")
        except Exception:
            return False

    async def update_data(self):
        _LOGGER.info("Initializing RaritanPDU")

        result = await self.snmp_manager.snmp_get(
            ["SNMPv2-MIB", "sysDescr", 0],
            ["SNMPv2-MIB", "sysName", 0],
            ["PDU-MIB", "outletEnergySupport", 0],
            ["PDU-MIB", "outletCount", 0],
            ["PDU-MIB", "unitCpuTemp", 0],  # The value for the unit's CPU temperature sensor in tenth degrees celsius.
        )

        if result is None:
            return  # abort update

        [desc, name, energy_support, outlet_count, cpu_temperature] = result

        self.name = str(desc).split(" - ")[0] + " " + str(name)
        self.energy_support = energy_support == "Yes"
        self.cpu_temperature = cpu_temperature / 10.0  # The value for the unit's CPU temperature sensor in tenth degrees celsius.

        # If the outlet count has changed, reinitialize the outlets list. This will run when first initialized.
        if outlet_count != self.outlet_count:
            self.outlet_count = outlet_count
            self.outlets = []
            for i in range(outlet_count):
                # Create an outlet (index starts from 1) and append it to the outlets list
                outlet = RaritanPDUOutlet(self.snmp_manager, i + 1, self.energy_support)
                self.outlets.append(outlet)

        # For each outlet, append all relevant MIB OIDs (using the key names from outlet.data)
        oids = []
        for outlet in self.outlets:
            for data_name in outlet.sensor_data.keys():
                mib_object_name = f"outlet{data_name.title().replace('_', '')}"
                oids.append(["PDU-MIB", mib_object_name, outlet.index])

        # Fetch all the outlet data in one go using the OIDs
        results = await self.snmp_manager.snmp_get(*oids)
        current_update_time = time.time()
        if result is None:
            return  # abort update

        # Update outlet data with the fetched results
        i = 0
        for outlet in self.outlets:
            for data_name in outlet.sensor_data.keys():
                # Update each data field in the outlet using the corresponding result
                outlet.sensor_data[data_name] = results[i]
                i += 1

            # Calculate energy first, then update timestamp
            outlet.update_energy_delivered(current_update_time)
            outlet.update_last_sensor_data_update_timestamp(current_update_time)

    def get_outlet_by_index(self, index: int) -> RaritanPDUOutlet:
        return self.outlets[index - 1]  # Outlet index starts from 1

    def get_data(self) -> dict:
        data = {}
        # Add data from outlets
        for outlet in self.outlets:
            data[outlet.index] = outlet.get_data()

        # Add data from PDU
        data["cpu_temperature"] = self.cpu_temperature
        return data
