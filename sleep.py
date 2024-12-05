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

# Ring service UUIDs
BIG_DATA_SERVICE = "DE5BF728-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_WRITE = "DE5BF72A-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_NOTIFY = "DE5BF729-D711-4E47-AF26-65E3012A5DC7"

# Command constants
BIG_DATA_MAGIC = 0xBC
SLEEP_DATA_ID = 0x27

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
    
    @property
    def light_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.LIGHT)

    def print_summary(self):
        print(f"\nSleep Summary for {self.date.strftime('%Y-%m-%d')}")
        print(f"Sleep Start: {self.sleep_start.strftime('%I:%M %p')}")
        print(f"Sleep End: {self.sleep_end.strftime('%I:%M %p')}")
        print(f"Total Sleep: {self.total_sleep_minutes // 60}h {self.total_sleep_minutes % 60}m")
        print(f"Deep Sleep: {self.deep_sleep_minutes // 60}h {self.deep_sleep_minutes % 60}m")
        print(f"Light Sleep: {self.light_sleep_minutes // 60}h {self.light_sleep_minutes % 60}m")
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

def parse_notification_data(data: bytearray) -> list[tuple[int, int]]:
    """Parse a single notification packet into list of (type, minutes) pairs"""
    periods = []
    i = 0
    while i < len(data) - 1:
        sleep_type = data[i]
        minutes = data[i + 1]
        if sleep_type in [2, 3, 5] and minutes > 0:  # Valid sleep types and duration
            periods.append((sleep_type, minutes))
        i += 2
    return periods

async def get_sleep_data(client: BleakClient) -> list[SleepDay]:
    """Request and parse sleep data from the ring"""
    # Create Big Data request packet
    packet = bytearray([
        BIG_DATA_MAGIC,     # Magic number
        SLEEP_DATA_ID,      # Sleep data ID
        0, 0,              # Data length (0 for request)
        0xFF, 0xFF         # CRC16 (0xFFFF for request)
    ])
    
    # Set up notification handling
    all_periods: list[tuple[int, int]] = []
    done_event = asyncio.Event()
    
    def notification_handler(sender: BleakGATTCharacteristic, data: bytearray):
        nonlocal all_periods
        logger.debug(f"Received notification: {data.hex()}")
        
        if data.startswith(bytes([BIG_DATA_MAGIC, SLEEP_DATA_ID])):
            # First packet - extract payload after header
            if 0x57 in data:  # Find data start marker
                start_idx = data.index(0x57) + 1
                periods = parse_notification_data(data[start_idx:])
                all_periods.extend(periods)
        else:
            # Subsequent packets - parse entire content
            periods = parse_notification_data(data)
            all_periods.extend(periods)
            
            # Check if this might be the last packet
            if len(data) < 20:  # Last packet is typically shorter
                done_event.set()
    
    try:
        # Get the Big Data characteristic
        big_data_service = client.services.get_service(BIG_DATA_SERVICE)
        if not big_data_service:
            logger.error("Big Data service not found")
            return []
            
        notify_char = big_data_service.get_characteristic(BIG_DATA_NOTIFY)
        write_char = big_data_service.get_characteristic(BIG_DATA_WRITE)
        
        if not notify_char or not write_char:
            logger.error("Required characteristics not found")
            return []
        
        # Enable notifications and send request
        logger.debug("Enabling notifications...")
        await client.start_notify(notify_char, notification_handler)
        
        logger.debug(f"Sending request: {packet.hex()}")
        await client.write_gatt_char(write_char, packet)
        
        # Wait for response data
        logger.debug("Waiting for response...")
        try:
            await asyncio.wait_for(done_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for sleep data")
            return []
            
        # Process the collected periods into sleep days
        if not all_periods:
            return []
            
        # Convert periods into SleepDay objects
        now = datetime.now(timezone.utc)
        base_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        current_time = base_time
        
        periods = []
        for sleep_type, minutes in all_periods:
            periods.append(SleepPeriod(
                type=SleepType(sleep_type),
                minutes=minutes,
                start_time=current_time
            ))
            current_time += timedelta(minutes=minutes)
        
        if periods:
            return [SleepDay(
                date=base_time,
                sleep_start=periods[0].start_time,
                sleep_end=periods[-1].start_time + timedelta(minutes=periods[-1].minutes),
                periods=periods
            )]
        return []
        
    finally:
        if notify_char:
            await client.stop_notify(notify_char)

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