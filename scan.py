#!/usr/bin/env python3

import asyncio
import logging
import re
import sys
from typing import Optional, List, Tuple
from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Ring name patterns using regex for more precise matching
RING_PATTERNS = [
    r"R0[1-7]_[A-Z0-9]+",  # Matches R01-R07 series with suffix
    r"R10_[A-Z0-9]+",      # Matches R10 series
    r"VK-5098",
    r"MERLIN",
    r"Hello Ring",
    r"RING1",
    r"boAtring",
    r"TR-R02",
    r"SE",
    r"EVOLVEO",
    r"GL-SR2",
    r"Blaupunkt",
    r"KSIX RING"
]

@dataclass
class DiscoveredDevice:
    """Class to store discovered device information in a structured way."""
    device: BLEDevice
    name: str
    address: str
    rssi: Optional[int]
    advertisement_data: AdvertisementData

    @classmethod
    def from_discovery(cls, device: BLEDevice, adv_data: AdvertisementData) -> 'DiscoveredDevice':
        """Create a DiscoveredDevice instance from scanner results."""
        return cls(
            device=device,
            name=device.name or "Unknown",
            address=device.address,
            rssi=adv_data.rssi,
            advertisement_data=adv_data
        )

    def is_compatible(self) -> bool:
        """Check if the device name matches any known compatible patterns."""
        if not self.name:
            return False
        return any(re.match(pattern, self.name) for pattern in RING_PATTERNS)

class RingScanner:
    """Class to handle ring device scanning and discovery."""
    
    def __init__(self):
        self.discovered_devices: List[DiscoveredDevice] = []

    async def scan(self, duration: float = 7.0, scan_all: bool = False) -> List[DiscoveredDevice]:
        """
        Scan for BLE devices.
        
        Args:
            duration: How long to scan for devices (in seconds)
            scan_all: If True, returns all discovered devices regardless of compatibility
            
        Returns:
            List of discovered devices, filtered by compatibility unless scan_all is True
        """
        logger.info(f"Starting {duration} second scan for {'all' if scan_all else 'compatible'} devices...")
        self.discovered_devices.clear()
        
        def detection_callback(device: BLEDevice, adv_data: AdvertisementData):
            """Callback function for device detection."""
            if device.name:  # Only process devices with names
                discovered = DiscoveredDevice.from_discovery(device, adv_data)
                self.discovered_devices.append(discovered)
        
        # Start scanning
        scanner = BleakScanner(detection_callback=detection_callback)
        await scanner.start()
        await asyncio.sleep(duration)
        await scanner.stop()
        
        # Filter results if not scanning for all devices
        if not scan_all:
            return [dev for dev in self.discovered_devices if dev.is_compatible()]
        return self.discovered_devices

    def print_device_table(self, devices: List[DiscoveredDevice]) -> None:
        """
        Print a formatted table of discovered devices.
        
        Args:
            devices: List of discovered devices to display
        """
        if not devices:
            print("\nNo devices found. Try moving closer or checking device power.")
            return

        # Calculate column widths based on content
        name_width = max(20, max(len(dev.name) for dev in devices))
        addr_width = max(40, max(len(dev.address) for dev in devices))
        
        # Print header
        print("\nFound device(s):")
        header = f"{'Name':>{name_width}}  | {'Address':<{addr_width}} | {'Signal'}"
        print(header)
        print("-" * len(header))
        
        # Print device rows
        for device in devices:
            rssi = device.rssi if device.rssi is not None else "N/A"
            print(f"{device.name:>{name_width}}  | {device.address:<{addr_width}} | {rssi:>4}")

    def print_compatibility_tips(self):
        """Print helpful tips for compatibility issues."""
        print("\nTroubleshooting Tips:")
        print("1. Ensure your ring is charged and powered on")
        print("2. Try moving the ring closer to your computer")
        print("3. If using Windows, check if Bluetooth is enabled")
        print("4. On macOS, try using the device name instead of address")
        print("5. Some devices may need to be unpaired from your phone first")

async def main():
    scanner = RingScanner()
    
    try:
        # First scan for compatible devices
        devices = await scanner.scan()
        scanner.print_device_table(devices)
        
        # If no compatible devices found, offer to scan for all
        if not devices:
            print("\nNo compatible devices found. Would you like to scan for all nearby BLE devices? (y/n)")
            response = input().lower()
            
            if response == 'y':
                print("\nScanning for all nearby BLE devices...")
                all_devices = await scanner.scan(scan_all=True)
                scanner.print_device_table(all_devices)
                
                if all_devices:
                    print("\nNote: If your ring appears above but isn't detected as compatible,")
                    print("please report the device name and address so we can update our detection patterns.")
                    scanner.print_compatibility_tips()
                else:
                    print("\nNo Bluetooth devices found at all. Please check your system's Bluetooth status.")

    except asyncio.CancelledError:
        print("\nScan cancelled by user")
    except Exception as e:
        logger.error(f"Error during scan: {e}")
        if logging.getLogger().level == logging.DEBUG:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    try:
        # Enable debug logging if requested
        if "--debug" in sys.argv:
            logging.getLogger().setLevel(logging.DEBUG)
            
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)