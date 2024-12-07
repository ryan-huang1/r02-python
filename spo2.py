#!/usr/bin/env python3

import asyncio
import logging
from datetime import datetime
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

# Command constants
CMD_START_HEART_RATE = 105  # 0x69
CMD_STOP_HEART_RATE = 106   # 0x6A
CMD_REALTIME_HR = 30        # 0x1E

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

# Create the necessary packets
START_SPO2_PACKET = make_packet(CMD_START_HEART_RATE, bytearray(b"\x03\x25"))
CONTINUE_PACKET = make_packet(CMD_REALTIME_HR, bytearray(b"3"))
STOP_SPO2_PACKET = make_packet(CMD_STOP_HEART_RATE, bytearray(b"\x03\x00\x00"))

class RingMonitor:
    def __init__(self):
        self.client = None
        self.readings = []
        self.running = False

    def notification_handler(self, characteristic: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming notifications from the ring."""
        try:
            if data[0] == CMD_START_HEART_RATE:
                kind = data[1]
                error_code = data[2]
                if error_code != 0:
                    logger.error(f"Error reading SPO2: {error_code}")
                    return
                
                value = data[3]
                if value != 0:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"{timestamp} - SPO2: {value}%")
                    self.readings.append(value)
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

    async def start_monitoring(self):
        """Start SPO2 monitoring."""
        if not self.client or not self.client.is_connected:
            raise Exception("Ring not connected")

        self.running = True
        logger.info("Starting SPO2 monitoring...")
        
        try:
            # Send start packet
            await self.client.write_gatt_char(UART_RX_CHAR_UUID, START_SPO2_PACKET)
            
            # Continue requesting readings
            while self.running:
                await self.client.write_gatt_char(UART_RX_CHAR_UUID, CONTINUE_PACKET)
                await asyncio.sleep(1)  # Wait a second between readings
                
        except Exception as e:
            logger.error(f"Error during monitoring: {e}")
        finally:
            # Always try to stop properly
            try:
                await self.client.write_gatt_char(UART_RX_CHAR_UUID, STOP_SPO2_PACKET)
            except:
                pass

    async def stop_monitoring(self):
        """Stop SPO2 monitoring."""
        self.running = False
        if self.client and self.client.is_connected:
            await self.client.write_gatt_char(UART_RX_CHAR_UUID, STOP_SPO2_PACKET)
            await self.client.disconnect()
            logger.info("Disconnected from ring")

async def main():
    monitor = RingMonitor()
    
    try:
        # Find the ring
        device = await monitor.find_ring()
        if not device:
            print("No compatible ring found nearby. Make sure it's charged and close to your computer.")
            return

        # Connect to the ring
        await monitor.connect(device)
        
        # Start monitoring
        print("Starting SPO2 monitoring. Press Ctrl+C to stop.")
        await monitor.start_monitoring()
        
    except KeyboardInterrupt:
        print("\nStopping monitoring...")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await monitor.stop_monitoring()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)