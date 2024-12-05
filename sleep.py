#!/usr/bin/env python3

import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import IntEnum
import logging
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

# Set up logging to help debug
logging.basicConfig(level=logging.WARNING)  # Change to DEBUG for more detailed logs
logger = logging.getLogger(__name__)

# Device name prefixes to identify R02 ring and compatible devices
DEVICE_NAME_PREFIXES = [
    "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R10",  # Basic R-series
    "VK-5098", "MERLIN", "Hello Ring", "RING1", "boAtring", "TR-R02", "SE",
    "EVOLVEO", "GL-SR2", "Blaupunkt", "KSIX RING",
]

# Ring service UUIDs
BIG_DATA_SERVICE = "DE5BF728-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_WRITE = "DE5BF72A-D711-4E47-AF26-65E3012A5DC7"
BIG_DATA_NOTIFY = "DE5BF729-D711-4E47-AF26-65E3012A5DC7"

# Command constants
BIG_DATA_MAGIC = 0xBC
SLEEP_DATA_ID = 0x27

class SleepType(IntEnum):
    NODATA = 0x00
    LIGHT = 0x02
    DEEP = 0x03
    REM = 0x04
    AWAKE = 0x05
    UNKNOWN = -1

    def to_string(self) -> str:
        return {
            SleepType.NODATA: "No Data",
            SleepType.LIGHT: "Light Sleep",
            SleepType.DEEP: "Deep Sleep",
            SleepType.REM: "REM Sleep",
            SleepType.AWAKE: "Awake",
            SleepType.UNKNOWN: "Unknown"
        }.get(self, f"Unknown ({self.value})")

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
        return sum(p.minutes for p in self.periods if p.type in [
            SleepType.LIGHT, SleepType.DEEP, SleepType.REM])

    @property
    def deep_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.DEEP)

    @property
    def light_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.LIGHT)

    @property
    def rem_sleep_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.REM)

    @property
    def awake_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.AWAKE)

    @property
    def unknown_minutes(self) -> int:
        return sum(p.minutes for p in self.periods if p.type == SleepType.UNKNOWN)

    def print_summary(self):
        print(f"\nSleep Summary for {self.date.strftime('%Y-%m-%d')}")
        print(f"Sleep Start: {self.sleep_start.strftime('%I:%M %p')}")
        print(f"Sleep End: {self.sleep_end.strftime('%I:%M %p')}")
        total_minutes = (self.sleep_end - self.sleep_start).total_seconds() // 60
        print(f"Total Sleep Time: {int(total_minutes // 60)}h {int(total_minutes % 60)}m")
        print(f"Deep Sleep: {self.deep_sleep_minutes // 60}h {self.deep_sleep_minutes % 60}m")
        print(f"Light Sleep: {self.light_sleep_minutes // 60}h {self.light_sleep_minutes % 60}m")
        print(f"REM Sleep: {self.rem_sleep_minutes // 60}h {self.rem_sleep_minutes % 60}m")
        print(f"Time Awake: {self.awake_minutes // 60}h {self.awake_minutes % 60}m")
        print(f"Unknown Sleep: {self.unknown_minutes // 60}h {self.unknown_minutes % 60}m")
        print("\nSleep Phases:")
        for period in self.periods:
            print(f"- {period.start_time.strftime('%I:%M %p')}: "
                  f"{period.type.to_string()} for {period.minutes} minutes")

async def find_ring(scan_time: float = 10.0) -> BLEDevice | None:
    """Scan for R02 ring or compatible devices."""
    print(f"Scanning for devices for {scan_time} seconds...")

    # Use a dictionary to store unique devices by address
    discovered_devices_dict = {}

    def callback(device: BLEDevice, advertisement_data):
        if device.name:
            # Only store the latest advertisement for each unique address
            discovered_devices_dict[device.address] = device

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(scan_time)
    await scanner.stop()

    # Convert dictionary values back to list
    devices = list(discovered_devices_dict.values())

    if not devices:
        print("\nNo devices found. Try moving the ring closer to your computer.")
        return None

    # Filter for compatible devices
    compatible_devices = [
        device for device in devices
        if device.name and any(device.name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES)
    ]

    if not compatible_devices:
        print("\nNo R02 devices found. Please ensure your ring is nearby and powered on.")
        return None

    selected_device = compatible_devices[0]
    print(f"\nConnecting to {selected_device.name} ({selected_device.address})")
    return selected_device

def decode_timestamp_unix(data: bytes) -> datetime:
    """Decode timestamp as a 4-byte little-endian Unix timestamp"""
    try:
        timestamp_int = int.from_bytes(data[:4], byteorder='little')
        timestamp = datetime.utcfromtimestamp(timestamp_int)
        print(f"Decoded timestamp: {timestamp.isoformat()}")
        return timestamp
    except Exception as e:
        logger.error(f"Error decoding timestamp: {e}")
        return datetime.now(timezone.utc)

def parse_sleep_records(data: bytes, start_offset: int = 0) -> list[tuple[SleepType, int, int]]:
    """Parse raw sleep record bytes into (type, duration, offset) tuples."""
    records = []
    i = 0

    print("\nParsing sleep records:")
    while i < len(data) - 1:
        raw_type = data[i]
        duration = data[i + 1]

        # Map raw type to SleepType
        sleep_type = SleepType(raw_type) if raw_type in SleepType._value2member_map_ else SleepType.UNKNOWN

        # Debug print all records
        print(f"Offset {start_offset + i:02d}: "
              f"Raw Type=0x{raw_type:02X}, Duration={duration} "
              f"({sleep_type.to_string()})")

        if duration > 0:
            records.append((sleep_type, duration, start_offset + i))

        i += 2  # Move to the next record (type + duration)
    return records

async def get_sleep_data(client: BleakClient) -> list[SleepDay]:
    """Request and parse sleep data from the ring"""
    packet = bytearray([
        BIG_DATA_MAGIC,     # Magic number
        SLEEP_DATA_ID,      # Sleep data ID
        0, 0,               # Data length (0 for request)
        0xFF, 0xFF          # CRC16 (0xFFFF for request)
    ])

    all_records: list[tuple[SleepType, int, int]] = []  # (sleep_type, duration, offset)
    done_event = asyncio.Event()
    first_packet = None
    timestamp = None
    incomplete_record = None  # Store incomplete record between packets

    def notification_handler(sender: BleakGATTCharacteristic, data: bytearray):
        nonlocal all_records, first_packet, timestamp, incomplete_record
        print(f"\nReceived packet ({len(data)} bytes): {data.hex()}")

        # Handle first packet
        if first_packet is None and data.startswith(bytes([BIG_DATA_MAGIC, SLEEP_DATA_ID])):
            first_packet = data
            # Extract metadata
            if 0x57 in data:  # Find data start marker
                start_idx = data.index(0x57) + 1
                print("Found data start marker at offset:", start_idx)

                # Get timestamp bytes
                if start_idx + 6 <= len(data):
                    ts_bytes = data[start_idx:start_idx+6]
                    timestamp = decode_timestamp_unix(ts_bytes)

                    # Parse sleep records from remainder of first packet
                    sleep_data = data[start_idx+6:]
                    if incomplete_record:
                        sleep_data = incomplete_record + sleep_data
                        incomplete_record = None
                    if len(sleep_data) % 2 != 0:
                        incomplete_record = sleep_data[-1:]
                        sleep_data = sleep_data[:-1]
                    records = parse_sleep_records(sleep_data, start_offset=start_idx+6)
                    all_records.extend(records)
                else:
                    incomplete_record = data[start_idx+6:]
        else:
            # Append subsequent sleep data
            sleep_data = data
            if incomplete_record:
                sleep_data = incomplete_record + data
                incomplete_record = None
            if len(sleep_data) % 2 != 0:
                incomplete_record = sleep_data[-1:]
                sleep_data = sleep_data[:-1]
            records = parse_sleep_records(sleep_data)
            all_records.extend(records)

            # Check if this might be the last packet
            if len(data) < 20:  # Last packet is typically shorter
                done_event.set()

    try:
        # Get the Big Data characteristic
        await client.get_services()
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

        # Process the collected records
        if not all_records:
            return []

        print("\nCollected Records:")
        for sleep_type, duration, offset in all_records:
            print(f"Offset {offset:02d}: {sleep_type.to_string()}, {duration} minutes")

        # Create sleep periods from the records
        if timestamp:
            # Adjust the date if sleep started in the evening
            now = datetime.now()
            if timestamp.hour > now.hour:
                adjusted_date = (now - timedelta(days=1)).date()
            else:
                adjusted_date = now.date()
            timestamp = timestamp.replace(year=adjusted_date.year, month=adjusted_date.month, day=adjusted_date.day)
            print(f"Adjusted timestamp: {timestamp.isoformat()}")
        else:
            timestamp = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)

        start_time = timestamp
        current_time = start_time

        periods = []
        for sleep_type, duration, _ in all_records:
            periods.append(SleepPeriod(
                type=sleep_type,
                minutes=duration,
                start_time=current_time
            ))
            current_time += timedelta(minutes=duration)

        sleep_end = current_time

        if periods:
            return [SleepDay(
                date=start_time.date(),
                sleep_start=periods[0].start_time,
                sleep_end=sleep_end,
                periods=periods
            )]
        return []

    finally:
        if 'notify_char' in locals():
            await client.stop_notify(notify_char)

async def main():
    device = await find_ring()
    if not device:
        print("No R02 ring found nearby. Make sure it's charged and close to your computer.")
        return

    print(f"Found ring: {device.name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            print("Connected to ring")
            sleep_data = await get_sleep_data(client)

            if not sleep_data:
                print("No sleep data available")
                return

            for day in sleep_data:
                day.print_summary()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
