#!/usr/bin/env python3

import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import IntEnum
import sys
import logging
import re
from typing import Optional

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Ring service UUIDs
BIG_DATA_SERVICE = "DE5BF728-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_WRITE = "DE5BF72A-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_NOTIFY = "DE5BF729-D711-4E47-AF26-65E3012A5DC7"

# Command constants
BIG_DATA_MAGIC = 0xBC
SLEEP_DATA_ID = 0x27

RING_PATTERNS = [r"R02_[A-Z0-9]+", r"R06_[A-Z0-9]+", r"R10_[A-Z0-9]+"]

class SleepType(IntEnum):
    NODATA = 0x00
    ERROR = 0x01
    LIGHT = 0x02
    DEEP = 0x03
    REM = 0x04
    AWAKE = 0x05
    MOTION = 0x10
    REST = 0x20
    UNKNOWN = -1

    def to_string(self) -> str:
        return {
            SleepType.NODATA: "No Data",
            SleepType.ERROR: "Error",
            SleepType.LIGHT: "Light Sleep",
            SleepType.DEEP: "Deep Sleep",
            SleepType.REM: "REM Sleep",
            SleepType.AWAKE: "Awake",
            SleepType.MOTION: "Motion",
            SleepType.REST: "Resting",
            SleepType.UNKNOWN: "Unknown"
        }.get(self, f"Unknown ({self.value})")

@dataclass
class SleepPeriod:
    type: SleepType
    duration: int
    offset: int

@dataclass
class SleepRecord:
    total_duration: int
    periods: list[SleepPeriod]

    def print_summary(self):
        print("\nSleep Record Summary:")
        print(f"Total Duration: {self.total_duration} minutes ({self.total_duration/60:.1f} hours)")
        
        # Group durations by type
        type_durations = {}
        for period in self.periods:
            if period.type not in type_durations:
                type_durations[period.type] = 0
            type_durations[period.type] += period.duration
        
        print("\nBreakdown by type:")
        for sleep_type, duration in type_durations.items():
            print(f"{sleep_type.to_string()}: {duration} minutes ({duration/60:.1f} hours)")
        
        print("\nDetailed Sleep Periods:")
        current_offset = 0
        for period in self.periods:
            time_str = f"+{current_offset}min"
            print(f"{time_str:>8}: {period.type.to_string():12} for {period.duration:3} minutes")
            current_offset += period.duration

def parse_sleep_data(packets: list[bytes]) -> Optional[SleepRecord]:
    if not packets:
        return None
        
    # First packet contains header
    first_packet = packets[0]
    if len(first_packet) < 8 or first_packet[0] != BIG_DATA_MAGIC or first_packet[1] != SLEEP_DATA_ID:
        logger.error(f"Invalid header packet: {first_packet.hex()}")
        return None
        
    # Get total data length from header
    data_length = int.from_bytes(first_packet[2:4], 'little')
    logger.info(f"Total data length: {data_length} bytes")
    
    # Extract sleep records from all packets
    sleep_periods = []
    total_duration = 0
    
    # Process first packet data (after 8-byte header)
    data = bytearray()
    data.extend(first_packet[8:])
    
    # Add subsequent packet data
    for packet in packets[1:]:
        data.extend(packet)
    
    # Parse sleep records
    i = 0
    while i < len(data) - 1:
        raw_type = data[i]
        duration = data[i + 1]
        
        if raw_type == 0x1D:  # End marker
            break
            
        try:
            sleep_type = SleepType(raw_type) if raw_type in SleepType._value2member_map_ else SleepType.UNKNOWN
        except ValueError:
            sleep_type = SleepType.UNKNOWN
            
        if duration > 0:
            sleep_periods.append(SleepPeriod(
                type=sleep_type,
                duration=duration,
                offset=i
            ))
            total_duration += duration
            
        i += 2
    
    return SleepRecord(total_duration=total_duration, periods=sleep_periods)

async def find_ring() -> Optional[BLEDevice]:
    print("Scanning for R02 ring...")
    devices = await BleakScanner.discover()
    for device in devices:
        if device.name and any(re.match(pattern, device.name) for pattern in RING_PATTERNS):
            return device
    return None

async def get_sleep_data(client: BleakClient) -> Optional[SleepRecord]:
    # Request packet
    packet = bytearray([BIG_DATA_MAGIC, SLEEP_DATA_ID, 0, 0, 0xFF, 0xFF])
    received_packets = []
    done_event = asyncio.Event()

    def notification_handler(sender: BleakGATTCharacteristic, data: bytearray):
        print(f"\nReceived packet ({len(data)} bytes): {data.hex()}")
        received_packets.append(data)
        if len(data) < 20:  # Short packet indicates end
            done_event.set()

    try:
        service = client.services.get_service(BIG_DATA_SERVICE)
        if not service:
            logger.error("Big Data service not found")
            return None

        notify_char = service.get_characteristic(BIG_DATA_NOTIFY)
        write_char = service.get_characteristic(BIG_DATA_WRITE)

        if not notify_char or not write_char:
            logger.error("Required characteristics not found")
            return None

        await client.start_notify(notify_char, notification_handler)
        await client.write_gatt_char(write_char, packet)
        
        try:
            await asyncio.wait_for(done_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for sleep data")
            return None

        if not received_packets:
            return None

        return parse_sleep_data(received_packets)

    finally:
        if 'notify_char' in locals():
            await client.stop_notify(notify_char)

async def main():
    device = await find_ring()
    if not device:
        print("No R02 ring found nearby. Make sure it's charged and close.")
        return

    print(f"Found ring: {device.name} ({device.address})")

    try:
        async with BleakClient(device, timeout=20.0) as client:
            print("Connected to ring")
            sleep_record = await get_sleep_data(client)
            
            if sleep_record:
                sleep_record.print_summary()
            else:
                print("No sleep data available")

    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)