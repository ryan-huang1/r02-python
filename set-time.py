#!/usr/bin/env python3

import asyncio
from datetime import datetime
import logging
import re
import sys
from zoneinfo import ZoneInfo
import time

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Ring service UUIDs
UART_SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Command constants
CMD_SET_TIME = 1

# Ring name patterns to match
RING_PATTERNS = [
    r"R02_[A-Z0-9]+",  # Matches patterns like R02_AC04
    r"R06_[A-Z0-9]+",  # Matches R06 variants
    r"R10_[A-Z0-9]+",  # Matches R10 variants
]

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

def create_time_packet(target_time: datetime) -> bytearray:
    """Create a time-setting packet for the given datetime."""
    data = bytearray(7)
    data[0] = ((target_time.year - 2000) // 10 << 4) | ((target_time.year - 2000) % 10)  # BCD year
    data[1] = (target_time.month // 10 << 4) | (target_time.month % 10)  # BCD month
    data[2] = (target_time.day // 10 << 4) | (target_time.day % 10)  # BCD day
    data[3] = (target_time.hour // 10 << 4) | (target_time.hour % 10)  # BCD hour
    data[4] = (target_time.minute // 10 << 4) | (target_time.minute % 10)  # BCD minute
    data[5] = (target_time.second // 10 << 4) | (target_time.second % 10)  # BCD second
    data[6] = 1  # English language setting
    
    return make_packet(CMD_SET_TIME, data)

async def find_ring() -> BLEDevice | None:
    """Scan for R02 ring or compatible devices"""
    logger.info("Scanning for compatible rings...")

    devices = await BleakScanner.discover()
    for device in devices:
        if device.name:
            logger.debug(f"Found device: {device.name} ({device.address})")
            # Check if device name matches any of our patterns
            if any(re.match(pattern, device.name) for pattern in RING_PATTERNS):
                logger.info(f"Found compatible ring: {device.name}")
                return device
    return None

async def set_ring_time(client: BleakClient, target_time: datetime = None):
    """Set the ring's time."""
    if target_time is None:
        # Get current local time with timezone awareness
        local_time = datetime.now()
        target_time = local_time.replace(tzinfo=datetime.now(ZoneInfo('America/Los_Angeles')).tzinfo)
    
    try:
        # Get the UART service
        services = client.services
        uart_service = services.get_service(UART_SERVICE_UUID)
        if not uart_service:
            logger.error("Required UART service not found on device")
            return
            
        rx_char = uart_service.get_characteristic(UART_RX_CHAR_UUID)
        if not rx_char:
            logger.error("Required RX characteristic not found on device")
            return
        
        # Create and send the time-setting packet
        time_packet = create_time_packet(target_time)
        await client.write_gatt_char(rx_char, time_packet, response=False)
        logger.info(f"Time set to {target_time} ({target_time.tzinfo})")
        
        # Wait a moment to ensure the command is processed
        await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"Error while setting time: {e}")

async def main():
    # Find the ring
    device = await find_ring()
    if not device:
        print("No R02 ring found nearby. Make sure it's charged and close to your computer.")
        return

    print(f"Found ring: {device.name} ({device.address})")

    # Connect to the ring
    try:
        async with BleakClient(device, timeout=20.0) as client:
            logger.info(f"Connected to {device.name}")
            await set_ring_time(client)
            print("Time set successfully")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)