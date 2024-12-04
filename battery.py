import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Constants for BLE communication
UART_SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
CMD_BATTERY = 3

# Ring device name prefixes - copied directly from working scan.py
DEVICE_NAME_PREFIXES = [
    "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R10",  # Basic R-series
    "VK-5098", "MERLIN", "Hello Ring", "RING1", "boAtring", "TR-R02", "SE",
    "EVOLVEO", "GL-SR2", "Blaupunkt", "KSIX RING",
]

@dataclass
class BatteryInfo:
    """Class to store battery information"""
    level: int
    charging: bool

def make_packet(command: int, sub_data: bytearray | None = None) -> bytearray:
    """Create a properly formatted packet for the ring"""
    packet = bytearray(16)  # Ring uses 16-byte packets
    packet[0] = command    # First byte is command
    
    if sub_data:
        assert len(sub_data) <= 14, "Sub data must be less than 14 bytes"
        for i, byte in enumerate(sub_data):
            packet[i + 1] = byte
    
    # Calculate checksum (sum of all bytes modulo 255)
    packet[-1] = sum(packet) & 255
    return packet

async def scan_for_rings(scan_time: float = 7.0, scan_all: bool = False) -> list[tuple[BLEDevice, AdvertisementData]]:
    """Scan for R02 compatible rings."""
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

def parse_battery_response(packet: bytearray) -> BatteryInfo:
    """Parse the battery response packet from the ring"""
    assert len(packet) == 16, "Invalid packet length"
    assert packet[0] == CMD_BATTERY, "Not a battery response packet"
    
    return BatteryInfo(
        level=packet[1],      # Battery percentage
        charging=bool(packet[2])  # Charging status
    )

async def get_battery_level(device: BLEDevice) -> None:
    """Get the battery level from a specific ring device"""
    try:
        async with BleakClient(device) as client:
            print(f"\nConnected to ring: {device.name} ({device.address})")
            
            # Get the characteristic objects
            uart_service = client.services.get_service(UART_SERVICE_UUID)
            if not uart_service:
                print("Error: UART service not found")
                return
                
            rx_char = uart_service.get_characteristic(UART_RX_CHAR_UUID)
            if not rx_char:
                print("Error: RX characteristic not found")
                return
            
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
                
                # Print battery status
                print(f"\nBattery Level: {battery_info.level}%")
                print(f"Charging: {'Yes' if battery_info.charging else 'No'}")
                
                if battery_info.level < 20:
                    print("\nWARNING: Battery level is low!")
                    
            except asyncio.TimeoutError:
                print("Timeout waiting for ring response")
            
            # Cleanup
            await client.stop_notify(UART_TX_CHAR_UUID)
            
    except Exception as e:
        print(f"Error connecting to ring: {e}")

async def main():
    # First scan for compatible devices
    devices = await scan_for_rings()
    print_device_table(devices)
    
    if not devices:
        print("\nNo compatible devices found. Would you like to scan for all nearby BLE devices? (y/n)")
        response = input().lower()
        if response == 'y':
            print("\nScanning for all nearby BLE devices...")
            all_devices = await scan_for_rings(scan_all=True)
            print_device_table(all_devices)
            return
        return
    
    # If multiple devices found, let user choose
    if len(devices) > 1:
        print("\nMultiple devices found. Please choose one by number:")
        for i, (device, _) in enumerate(devices, 1):
            print(f"{i}. {device.name} ({device.address})")
        
        while True:
            try:
                choice = int(input("\nEnter device number: ")) - 1
                if 0 <= choice < len(devices):
                    selected_device = devices[choice][0]
                    break
                print("Invalid choice. Please try again.")
            except ValueError:
                print("Please enter a number.")
    else:
        selected_device = devices[0][0]
    
    # Get battery level for selected device
    await get_battery_level(selected_device)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
    except Exception as e:
        print(f"\nError occurred: {e}")