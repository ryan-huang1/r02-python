#!/usr/bin/env python3

import asyncio
from datetime import datetime
import logging
import sys
import re
from dataclasses import dataclass

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Ring service UUIDs
UART_SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Command constants
CMD_BATTERY = 3

# Ring name patterns to match
RING_PATTERNS = [
    r"R02_[A-Z0-9]+",  # Matches patterns like R02_AC04
    r"R06_[A-Z0-9]+",  # Matches R06 variants
    r"R10_[A-Z0-9]+",  # Matches R10 variants
]

@dataclass
class BatteryInfo:
    """Class to store battery information"""
    battery_level: int
    charging: bool

def make_packet(command: int, sub_data: bytearray | None = None) -> bytearray:
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

def parse_battery_response(packet: bytearray) -> BatteryInfo:
    """Parse the battery response packet from the ring."""
    assert len(packet) == 16, f"Invalid packet length: {len(packet)}"
    assert packet[0] == CMD_BATTERY, "Not a battery response packet"
    
    return BatteryInfo(
        battery_level=packet[1],
        charging=bool(packet[2])
    )

async def find_ring() -> BLEDevice | None:
    """Scan for R02 ring or compatible devices."""
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

async def get_battery_level(client: BleakClient) -> BatteryInfo:
    """Get the battery level from the connected ring."""
    try:
        # Get the UART service
        services = client.services
        uart_service = services.get_service(UART_SERVICE_UUID)
        if not uart_service:
            raise RuntimeError("Required UART service not found on device")
            
        rx_char = uart_service.get_characteristic(UART_RX_CHAR_UUID)
        if not rx_char:
            raise RuntimeError("Required RX characteristic not found on device")
        
        # Create a queue for responses
        response_queue = asyncio.Queue()
        
        # Callback for handling notifications
        def notification_handler(_, data: bytearray):
            asyncio.create_task(response_queue.put(data))
        
        # Subscribe to notifications
        await client.start_notify(UART_TX_CHAR_UUID, notification_handler)
        
        # Create and send battery request packet
        battery_packet = make_packet(CMD_BATTERY)
        await client.write_gatt_char(rx_char, battery_packet, response=False)
        
        # Wait for response
        try:
            response = await asyncio.wait_for(response_queue.get(), timeout=5.0)
            battery_info = parse_battery_response(response)
            return battery_info
            
        except asyncio.TimeoutError:
            raise RuntimeError("Timeout waiting for ring response")
        finally:
            # Always cleanup the notification handler
            await client.stop_notify(UART_TX_CHAR_UUID)
            
    except Exception as e:
        logger.error(f"Error while getting battery level: {e}")
        raise

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
            battery_info = await get_battery_level(client)
            
            # Print battery status with nice formatting
            print("\nBattery Status:")
            print("-" * 20)
            print(f"Level: {battery_info.battery_level}%")
            print(f"Charging: {'Yes' if battery_info.charging else 'No'}")
            
            # Add warning for low battery
            if battery_info.battery_level < 20:
                print("\nWARNING: Battery level is low!")
                
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)