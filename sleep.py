#!/usr/bin/env python3

import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import IntEnum
import sys
import logging
import re

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

# Set up logging to help debug
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Big Data protocol constants
BIG_DATA_SERVICE = "DE5BF728-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_WRITE = "DE5BF72A-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_NOTIFY = "DE5BF729-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_MAGIC = 188
SLEEP_DATA_ID = 39

# Ring name patterns to match
RING_PATTERNS = [
    r"R02_[A-Z0-9]+",  # Matches patterns like R02_AC04
    r"R06_[A-Z0-9]+",  # Matches R06 variants
    r"R10_[A-Z0-9]+",  # Matches R10 variants
]

class SleepType(IntEnum):
    NODATA = 0
    ERROR = 1
    LIGHT = 2
    DEEP = 3
    AWAKE = 5

    def to_string(self) -> str:
        return {
            SleepType.NODATA: "No Data",
            SleepType.ERROR: "Error",
            SleepType.LIGHT: "Light Sleep",
            SleepType.DEEP: "Deep Sleep",
            SleepType.AWAKE: "Awake"
        }[self]

@dataclass
class SleepPeriod:
    type: SleepType
    minutes: int
    start_time: datetime

@dataclass
class SleepDay:
    date: datetime
    sleep_start: datetime
    sleep_end: datetime
    periods: list[SleepPeriod]
    
    @property
    def total_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type in [SleepType.LIGHT, SleepType.DEEP])
    
    @property
    def deep_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.DEEP)

    def print_summary(self):
        print(f"\nSleep Summary for {self.date.strftime('%Y-%m-%d')}")
        print(f"Sleep Start: {self.sleep_start.strftime('%I:%M %p')}")
        print(f"Sleep End: {self.sleep_end.strftime('%I:%M %p')}")
        print(f"Total Sleep: {self.total_sleep_minutes // 60}h {self.total_sleep_minutes % 60}m")
        print(f"Deep Sleep: {self.deep_sleep_minutes // 60}h {self.deep_sleep_minutes % 60}m")
        print("\nSleep Phases:")
        for period in self.periods:
            print(f"- {period.start_time.strftime('%I:%M %p')}: "
                  f"{period.type.to_string()} for {period.minutes} minutes")

async def find_ring() -> BLEDevice | None:
    """Scan for R02 ring or compatible devices"""
    print("Scanning for R02 ring...")
    
    devices = await BleakScanner.discover()
    for device in devices:
        if device.name:
            logger.debug(f"Found device: {device.name} ({device.address})")
            # Check if device name matches any of our patterns
            if any(re.match(pattern, device.name) for pattern in RING_PATTERNS):
                logger.debug(f"Found matching ring: {device.name}")
                return device
    return None

async def get_sleep_data(client: BleakClient) -> list[SleepDay]:
    """Request and parse sleep data from the ring"""
    # Create Big Data request packet
    packet = bytearray([
        BIG_DATA_MAGIC,  # Magic number
        SLEEP_DATA_ID,   # Sleep data ID
        0, 0,           # Data length (0 for request)
        0xFF, 0xFF      # CRC16 (0xFFFF for request)
    ])
    
    # Set up notification handler and response future
    response_data = bytearray()
    response_event = asyncio.Event()
    
    def notification_handler(sender: BleakGATTCharacteristic, data: bytearray):
        nonlocal response_data
        logger.debug(f"Received notification: {data.hex()}")
        response_data.extend(data)
        if len(response_data) >= 6:  # We have at least the header
            data_len = (response_data[2] << 8) | response_data[3]
            if len(response_data) >= data_len + 6:  # We have all the data
                response_event.set()
    
    try:
        # Get the services and characteristics
        logger.debug("Getting services...")
        services = client.services
        for service in services:
            logger.debug(f"Found service: {service.uuid}")
            for char in service.characteristics:
                logger.debug(f"  Characteristic: {char.uuid}")
        
        big_data_service = services.get_service(BIG_DATA_SERVICE)
        if not big_data_service:
            logger.error("Big Data service not found")
            return []
        
        notify_char = big_data_service.get_characteristic(BIG_DATA_NOTIFY)
        write_char = big_data_service.get_characteristic(BIG_DATA_WRITE)
        
        if not notify_char or not write_char:
            logger.error("Required characteristics not found")
            return []
        
        # Enable notifications
        logger.debug("Enabling notifications...")
        await client.start_notify(notify_char, notification_handler)
        
        # Send request
        logger.debug(f"Sending request: {packet.hex()}")
        await client.write_gatt_char(write_char, packet)
        
        # Wait for response with timeout
        logger.debug("Waiting for response...")
        try:
            await asyncio.wait_for(response_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for sleep data")
            return []
        
        # Parse response
        logger.debug(f"Received complete response: {response_data.hex()}")
        return parse_sleep_data(response_data)
        
    finally:
        # Always clean up notifications
        if notify_char:
            await client.stop_notify(notify_char)

def parse_sleep_data(packet: bytearray) -> list[SleepDay]:
    """Parse sleep data from the Big Data protocol response"""
    try:
        assert len(packet) >= 6, "Packet too short"
        assert packet[0] == BIG_DATA_MAGIC, "Invalid magic number"
        assert packet[1] == SLEEP_DATA_ID, "Invalid sleep data ID"
        
        num_days = packet[6]
        logger.debug(f"Number of days in response: {num_days}")
        
        sleep_days = []
        offset = 7
        
        for _ in range(num_days):
            days_ago = packet[offset]
            num_bytes = packet[offset + 1]
            sleep_start_minutes = (packet[offset + 2] << 8) | packet[offset + 3]
            sleep_end_minutes = (packet[offset + 4] << 8) | packet[offset + 5]
            
            logger.debug(f"Parsing day {days_ago} days ago, {num_bytes} bytes")
            
            # Calculate actual dates
            base_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
            base_date = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            sleep_start = base_date + timedelta(minutes=sleep_start_minutes)
            sleep_end = base_date + timedelta(minutes=sleep_end_minutes)
            
            # Parse sleep periods
            periods = []
            period_offset = offset + 6
            current_time = sleep_start
            
            while period_offset < offset + num_bytes:
                sleep_type = SleepType(packet[period_offset])
                minutes = packet[period_offset + 1]
                
                periods.append(SleepPeriod(
                    type=sleep_type,
                    minutes=minutes,
                    start_time=current_time
                ))
                
                current_time += timedelta(minutes=minutes)
                period_offset += 2
                
            sleep_days.append(SleepDay(
                date=base_date,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
                periods=periods
            ))
            
            offset += num_bytes
            
        return sleep_days
    except Exception as e:
        logger.error(f"Error parsing sleep data: {e}")
        return []

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
            print("Connected to ring")
            
            # Get sleep data
            sleep_data = await get_sleep_data(client)
            
            if not sleep_data:
                print("No sleep data available")
                return
            
            # Print sleep data
            for day in sleep_data:
                day.print_summary()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)