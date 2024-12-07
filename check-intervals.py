#!/usr/bin/env python3

import asyncio
import logging
from dataclasses import dataclass
import sys
import re

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ring service UUIDs
UART_SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Command IDs
CMD_HEART_RATE_LOG_SETTINGS = 22  # 0x16
CMD_BLOOD_OXYGEN = 44
CMD_PRESSURE = 54
CMD_HRV = 56

# Ring name patterns to match
RING_PATTERNS = [
    r"R02_[A-Z0-9]+",  # Matches patterns like R02_AC04
    r"R06_[A-Z0-9]+",  # Matches R06 variants
    r"R10_[A-Z0-9]+",  # Matches R10 variants
]

@dataclass
class SensorSettings:
    enabled: bool
    interval: int = 0  # in minutes, if applicable

def make_packet(command: int, sub_data=None) -> bytearray:
    """Create a properly formatted packet for the ring."""
    packet = bytearray(16)
    packet[0] = command
    
    if sub_data:
        assert len(sub_data) <= 14, "Sub data must be less than 14 bytes"
        for i, byte in enumerate(sub_data):
            packet[i + 1] = byte
    
    # Calculate checksum (sum of all bytes modulo 256)
    packet[-1] = sum(packet[:-1]) & 0xFF
    return packet

class RingSettingsChecker:
    def __init__(self):
        self.client = None
        self.settings = {}
        self.response_event = asyncio.Event()
        self.current_command = None

    def notification_handler(self, characteristic: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming notifications from the ring."""
        try:
            command = data[0]
            if command != self.current_command:
                return

            if command == CMD_HEART_RATE_LOG_SETTINGS:
                raw_enabled = data[2]
                enabled = True if raw_enabled == 1 else False
                interval = data[3]
                self.settings['heart_rate'] = SensorSettings(enabled=enabled, interval=interval)
            
            elif command == CMD_BLOOD_OXYGEN:
                enabled = data[2] == 1
                self.settings['blood_oxygen'] = SensorSettings(enabled=enabled)
            
            elif command == CMD_PRESSURE:
                enabled = data[2] == 1
                self.settings['pressure'] = SensorSettings(enabled=enabled)
            
            elif command == CMD_HRV:
                enabled = data[2] == 1
                self.settings['hrv'] = SensorSettings(enabled=enabled)

            self.response_event.set()

        except Exception as e:
            logger.error(f"Error processing notification: {e}")

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
        """Connect to the ring and set up notifications."""
        self.client = BleakClient(device)
        await self.client.connect()
        logger.info(f"Connected to {device.name}")

        # Start notifications
        await self.client.start_notify(UART_TX_CHAR_UUID, self.notification_handler)

    async def check_settings(self):
        """Check settings for all sensors."""
        if not self.client or not self.client.is_connected:
            raise Exception("Ring not connected")

        # Check heart rate logging settings
        self.current_command = CMD_HEART_RATE_LOG_SETTINGS
        self.response_event.clear()
        await self.client.write_gatt_char(
            UART_RX_CHAR_UUID,
            make_packet(CMD_HEART_RATE_LOG_SETTINGS, bytearray(b"\x01"))
        )
        await asyncio.wait_for(self.response_event.wait(), timeout=2.0)

        # Check blood oxygen settings
        self.current_command = CMD_BLOOD_OXYGEN
        self.response_event.clear()
        await self.client.write_gatt_char(
            UART_RX_CHAR_UUID,
            make_packet(CMD_BLOOD_OXYGEN, bytearray(b"\x01"))
        )
        await asyncio.wait_for(self.response_event.wait(), timeout=2.0)

        # Check pressure settings
        self.current_command = CMD_PRESSURE
        self.response_event.clear()
        await self.client.write_gatt_char(
            UART_RX_CHAR_UUID,
            make_packet(CMD_PRESSURE, bytearray(b"\x01"))
        )
        await asyncio.wait_for(self.response_event.wait(), timeout=2.0)

        # Check HRV settings
        self.current_command = CMD_HRV
        self.response_event.clear()
        await self.client.write_gatt_char(
            UART_RX_CHAR_UUID,
            make_packet(CMD_HRV, bytearray(b"\x01"))
        )
        await asyncio.wait_for(self.response_event.wait(), timeout=2.0)

    def print_settings(self):
        """Print the current settings in a formatted way."""
        print("\nCurrent Sensor Settings:")
        print("-" * 40)
        
        if 'heart_rate' in self.settings:
            hr = self.settings['heart_rate']
            print(f"Heart Rate Monitoring:")
            print(f"  Enabled: {hr.enabled}")
            print(f"  Interval: {hr.interval} minutes")
        
        if 'blood_oxygen' in self.settings:
            spo2 = self.settings['blood_oxygen']
            print(f"Blood Oxygen (SPO2) Monitoring:")
            print(f"  Enabled: {spo2.enabled}")
        
        if 'pressure' in self.settings:
            pressure = self.settings['pressure']
            print(f"Pressure Monitoring:")
            print(f"  Enabled: {pressure.enabled}")
        
        if 'hrv' in self.settings:
            hrv = self.settings['hrv']
            print(f"HRV Monitoring:")
            print(f"  Enabled: {hrv.enabled}")
        
        print("-" * 40)

    async def disconnect(self):
        """Disconnect from the ring."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from ring")

async def main():
    checker = RingSettingsChecker()
    
    try:
        # Find the ring
        device = await checker.find_ring()
        if not device:
            print("No compatible ring found nearby. Make sure it's charged and close to your computer.")
            return

        # Connect to the ring
        await checker.connect(device)
        
        # Check settings
        print("Checking sensor settings...")
        await checker.check_settings()
        
        # Print results
        checker.print_settings()
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await checker.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)