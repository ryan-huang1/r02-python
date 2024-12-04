import asyncio
from typing import Optional
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

DEVICE_NAME_PREFIXES = [
    "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R10",  # Basic R-series
    "VK-5098",
    "MERLIN",
    "Hello Ring",
    "RING1",
    "boAtring",
    "TR-R02",
    "SE",
    "EVOLVEO",
    "GL-SR2",
    "Blaupunkt",
    "KSIX RING",
]

async def scan_for_rings(scan_time: float = 7.0, scan_all: bool = False) -> list[tuple[BLEDevice, AdvertisementData]]:
    """
    Scan for R02 compatible rings.
    
    Args:
        scan_time: How long to scan for devices (in seconds)
        scan_all: If True, returns all discovered devices regardless of name prefix
    """
    print(f"Scanning for devices for {scan_time} seconds...")
    
    # Using the newer detection_callback style scanning
    discovered_devices = []
    
    def callback(device: BLEDevice, advertisement_data: AdvertisementData):
        if device.name:  # Only store devices with names
            discovered_devices.append((device, advertisement_data))
    
    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(scan_time)
    await scanner.stop()
    
    if scan_all:
        return discovered_devices
    
    # Filter for compatible devices
    return [
        (device, adv) for device, adv in discovered_devices 
        if device.name and any(device.name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES)
    ]

def print_device_table(devices: list[tuple[BLEDevice, AdvertisementData]]) -> None:
    """Print a formatted table of discovered devices."""
    if not devices:
        print("\nNo devices found. Try moving the ring closer to your computer.")
        return

    print("\nFound device(s):")
    print(f"{'Name':>20}  | {'UUID':40} | {'Signal'}")
    print("-" * 75)
    
    for device, advertisement_data in devices:
        name = device.name or "Unknown"
        rssi = advertisement_data.rssi if advertisement_data.rssi is not None else "N/A"
        print(f"{name:>20}  | {device.address:40} | {rssi:>4}")

async def main():
    # First scan for just compatible devices
    devices = await scan_for_rings()
    print_device_table(devices)
    
    # Optionally scan for all devices if no compatible ones found
    if not devices:
        print("\nNo compatible devices found. Would you like to scan for all nearby BLE devices? (y/n)")
        response = input().lower()
        if response == 'y':
            print("\nScanning for all nearby BLE devices...")
            all_devices = await scan_for_rings(scan_all=True)
            print_device_table(all_devices)
            
            if all_devices:
                print("\nTip: If your ring is in the list above but not being detected as compatible,")
                print("it might be using a different name format. Please note the name and address")
                print("and share them so we can update the detection patterns.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScan interrupted by user")
    except Exception as e:
        print(f"\nError occurred: {e}")