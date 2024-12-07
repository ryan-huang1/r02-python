#!/usr/bin/env python3

import asyncio
import logging
import sys
import re

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Service and Characteristic UUIDs
DEVICE_INFO_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
MANUFACTURER_UUID = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
HW_VERSION_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
FW_VERSION_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
SERIAL_UUID = "00002a25-0000-1000-8000-00805f9b34fb"

# Ring name patterns to match
RING_PATTERNS = [
    r"R02_[A-Z0-9]+",  # Matches patterns like R02_AC04
    r"R06_[A-Z0-9]+",  # Matches R06 variants
    r"R10_[A-Z0-9]+",  # Matches R10 variants
]

class DeviceInfoReader:
    def __init__(self):
        self.client = None
        self.device_info = {}

    async def find_ring(self) -> BLEDevice | None:
        """Scan for compatible ring devices."""
        logger.info("Scanning for compatible rings...")
        
        devices = await BleakScanner.discover()
        for device in devices:
            if device.name:
                if any(re.match(pattern, device.name) for pattern in RING_PATTERNS):
                    logger.info(f"Found compatible ring: {device.name}")
                    return device
        return None

    async def connect(self, device: BLEDevice):
        """Connect to the ring."""
        self.client = BleakClient(device)
        await self.client.connect()
        logger.info(f"Connected to {device.name}")

    def format_value(self, value) -> str:
        """Format a characteristic value for display."""
        if not value:
            return "Not available"
        
        if isinstance(value, bytearray) or isinstance(value, bytes):
            try:
                decoded = value.decode('utf-8').strip()
                return decoded if decoded else "Not available"
            except UnicodeDecodeError:
                # If UTF-8 decoding fails, try to represent it as hex
                return value.hex()
        
        return str(value).strip() or "Not available"

    async def read_characteristic_safe(self, uuid: str, name: str) -> str:
        """Safely read a characteristic and handle errors."""
        try:
            value = await self.client.read_gatt_char(uuid)
            return self.format_value(value)
        except Exception as e:
            logger.debug(f"Could not read {name}: {e}")
            return "Not available"

    async def get_device_info(self):
        """Read all available device information."""
        if not self.client or not self.client.is_connected:
            raise Exception("Ring not connected")

        # Get the device information service
        service = self.client.services.get_service(DEVICE_INFO_UUID)
        if not service:
            raise Exception("Device Information service not found")

        # Read all available characteristics
        self.device_info = {
            "Manufacturer": await self.read_characteristic_safe(MANUFACTURER_UUID, "manufacturer"),
            "Model": await self.read_characteristic_safe(MODEL_UUID, "model"),
            "Hardware Version": await self.read_characteristic_safe(HW_VERSION_UUID, "hardware version"),
            "Firmware Version": await self.read_characteristic_safe(FW_VERSION_UUID, "firmware version"),
            "Serial Number": await self.read_characteristic_safe(SERIAL_UUID, "serial number")
        }

        # Parse the firmware version if it matches the expected format
        fw_version = self.device_info["Firmware Version"]
        if fw_version.startswith("RY02_"):
            try:
                # Extract version and date
                parts = fw_version.split('_')
                if len(parts) >= 3:
                    version = parts[1]
                    date = parts[2]
                    if len(date) == 6:
                        formatted_date = f"20{date[0:2]}-{date[2:4]}-{date[4:6]}"
                        self.device_info["Build Date"] = formatted_date
            except Exception as e:
                logger.debug(f"Error parsing firmware version: {e}")

    def print_device_info(self):
        """Print the device information in a formatted way."""
        print("\nDevice Information:")
        print("-" * 50)
        max_key_length = max(len(key) for key in self.device_info.keys())
        
        for key, value in self.device_info.items():
            print(f"{key.ljust(max_key_length)}: {value}")
        print("-" * 50)

    async def disconnect(self):
        """Disconnect from the ring."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from ring")

async def main():
    reader = DeviceInfoReader()
    
    try:
        # Find the ring
        device = await reader.find_ring()
        if not device:
            print("No compatible ring found nearby. Make sure it's charged and close to your computer.")
            return

        # Connect to the ring
        await reader.connect(device)
        
        # Get and display device information
        print("Reading device information...")
        await reader.get_device_info()
        reader.print_device_info()
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
    finally:
        await reader.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)