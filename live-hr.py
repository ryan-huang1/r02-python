import asyncio
from typing import Optional, Tuple
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.characteristic import BleakGATTCharacteristic
import struct

DEVICE_NAME_PREFIXES = [
    "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R10",  # Basic R-series
    "VK-5098", "MERLIN", "Hello Ring", "RING1", "boAtring", "TR-R02", "SE",
    "EVOLVEO", "GL-SR2", "Blaupunkt", "KSIX RING",
]

# UART Service UUIDs
UART_SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Command constants for heart rate monitoring
CMD_START_HEART_RATE = 105  # 0x69
CMD_REAL_TIME_HEART_RATE = 30  # 0x1E
CMD_STOP_HEART_RATE = 106  # 0x6A

class R02RingClient:
    def __init__(self, device: BLEDevice):
        self.device = device
        self.client = BleakClient(device)
        self.rx_char = None
        self.heart_rate_queue = asyncio.Queue()
        
    def make_packet(self, command: int, sub_data: bytearray | None = None) -> bytearray:
        """Create a properly formatted packet for the ring."""
        packet = bytearray(16)
        packet[0] = command
        
        if sub_data:
            assert len(sub_data) <= 14, "Sub data must be less than 14 bytes"
            for i in range(len(sub_data)):
                packet[i + 1] = sub_data[i]
                
        # Calculate checksum
        packet[-1] = sum(packet) & 255
        return packet
    
    async def connect(self):
        """Connect to the ring and set up the UART service."""
        print(f"Connecting to {self.device.name} ({self.device.address})...")
        await self.client.connect()
        
        # Get the UART service
        uart_service = self.client.services.get_service(UART_SERVICE_UUID)
        if not uart_service:
            raise RuntimeError("UART service not found")
            
        # Get the RX characteristic for sending commands
        self.rx_char = uart_service.get_characteristic(UART_RX_CHAR_UUID)
        if not self.rx_char:
            raise RuntimeError("RX characteristic not found")
            
        # Start notifications for the TX characteristic
        await self.client.start_notify(UART_TX_CHAR_UUID, self._handle_heart_rate_data)
        print("Connected successfully!")
        
    async def disconnect(self):
        """Disconnect from the ring."""
        if self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected from ring")
        
    def _handle_heart_rate_data(self, _: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming heart rate data packets."""
        if len(data) == 16 and data[0] == CMD_START_HEART_RATE:
            kind = data[1]
            error_code = data[2]
            if error_code == 0:
                hr_value = data[3]
                if hr_value != 0:
                    self.heart_rate_queue.put_nowait(hr_value)
                    
    async def start_heart_rate_monitoring(self):
        """Start real-time heart rate monitoring."""
        # Send start heart rate command
        start_packet = self.make_packet(CMD_START_HEART_RATE, bytearray(b"\x01\x00"))
        await self.client.write_gatt_char(self.rx_char, start_packet)
        
        # Continue packet for maintaining the measurement
        continue_packet = self.make_packet(CMD_REAL_TIME_HEART_RATE, bytearray(b"3"))
        
        print("Starting heart rate monitoring...")
        try:
            while True:
                try:
                    # Wait for heart rate data
                    hr = await asyncio.wait_for(self.heart_rate_queue.get(), timeout=2.0)
                    print(f"Heart Rate: {hr} BPM")
                    
                    # Send continue packet to maintain measurements
                    await self.client.write_gatt_char(self.rx_char, continue_packet)
                except asyncio.TimeoutError:
                    # If no data received, send continue packet
                    await self.client.write_gatt_char(self.rx_char, continue_packet)
        except asyncio.CancelledError:
            # Send stop command when monitoring is cancelled
            stop_packet = self.make_packet(CMD_STOP_HEART_RATE, bytearray(b"\x01\x00\x00"))
            await self.client.write_gatt_char(self.rx_char, stop_packet)
            raise

async def scan_for_rings(scan_time: float = 7.0, scan_all: bool = False) -> list[tuple[BLEDevice, AdvertisementData]]:
    """Scan for R02 compatible rings."""
    print(f"Scanning for devices for {scan_time} seconds...")
    
    # Use a dictionary to store unique devices by address
    discovered_devices_dict = {}
    
    def callback(device: BLEDevice, advertisement_data: AdvertisementData):
        if device.name:
            # Only store the latest advertisement for each unique address
            discovered_devices_dict[device.address] = (device, advertisement_data)
    
    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(scan_time)
    await scanner.stop()
    
    # Convert dictionary values back to list
    discovered_devices = list(discovered_devices_dict.values())
    
    if scan_all:
        return discovered_devices
    
    # Filter for compatible devices with more explicit debugging
    compatible_devices = []
    for device, adv in discovered_devices:
        if device.name:
            is_compatible = any(device.name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES)
            if is_compatible:
                compatible_devices.append((device, adv))
                print(f"Found compatible device: {device.name} ({device.address})")
    
    return compatible_devices

def print_device_table(devices: list[tuple[BLEDevice, AdvertisementData]]) -> None:
    """Print a formatted table of discovered devices."""
    if not devices:
        print("\nNo devices found. Try moving the ring closer to your computer.")
        return

    # Remove duplicates based on device address
    unique_devices = {}
    for device, adv in devices:
        if device.address not in unique_devices:
            unique_devices[device.address] = (device, adv)
    
    devices = list(unique_devices.values())

    print("\nFound device(s):")
    print(f"{'Name':>20}  | {'Address':40} | {'Signal'}")
    print("-" * 75)
    
    for device, advertisement_data in sorted(devices, key=lambda x: x[0].name or ""):
        name = device.name or "Unknown"
        rssi = advertisement_data.rssi if advertisement_data.rssi is not None else "N/A"
        print(f"{name:>20}  | {device.address:40} | {rssi:>4}")

async def main():
    # First scan for compatible devices with a longer scan time
    devices = await scan_for_rings(scan_time=10.0)  # Increased scan time
    
    if devices:
        print("\nFound compatible R02 devices:")
        print_device_table(devices)
    else:
        print("\nNo R02 devices found initially. Scanning for all nearby devices...")
        all_devices = await scan_for_rings(scan_all=True)
        print_device_table(all_devices)
        
        # Check if any R02 devices are in the all_devices list that we missed
        r02_devices = [(d, adv) for d, adv in all_devices 
                      if d.name and any(d.name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES)]
        
        if r02_devices:
            print("\nFound R02 devices in complete scan:")
            print_device_table(r02_devices)
            devices = r02_devices
        else:
            print("\nNo R02 devices found. Please ensure your ring is nearby and powered on.")
            return

    # If devices were found, let user select one
    if len(devices) > 1:
        print("\nMultiple devices found. Please enter the number of the device to connect to:")
        for i, (device, _) in enumerate(devices):
            print(f"{i}: {device.name} ({device.address})")
        
        while True:
            try:
                choice = input("Device number: ")
                device_index = int(choice)
                if 0 <= device_index < len(devices):
                    selected_device = devices[device_index][0]
                    break
                else:
                    print(f"Please enter a number between 0 and {len(devices)-1}")
            except ValueError:
                print("Please enter a valid number")
    else:
        selected_device = devices[0][0]
        print(f"\nConnecting to {selected_device.name} ({selected_device.address})")

    # Create client and connect to selected device
    ring = R02RingClient(selected_device)  # Pass the entire device object
    try:
        await ring.connect()
        print("\nPress Ctrl+C to stop heart rate monitoring")
        monitoring_task = asyncio.create_task(ring.start_heart_rate_monitoring())
        await monitoring_task
    except asyncio.CancelledError:
        print("\nStopping heart rate monitoring...")
    except Exception as e:
        print(f"\nError occurred: {e}")
        raise  # Re-raise the exception to see the full error traceback
    finally:
        await ring.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user")
    except Exception as e:
        print(f"\nError occurred: {e}")