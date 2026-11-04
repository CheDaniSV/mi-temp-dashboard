import asyncio
import struct
from bleak import BleakClient

MAC = "A4:C1:38:CC:2D:CB"
UUID = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"

def on_data(sender, data):
    temp, humidity, voltage = struct.unpack("<hBH", data)
    print(f"{temp / 100:.2f} °C | {humidity}% | {voltage} mV")

async def main():
    async with BleakClient(MAC) as client:
        await client.start_notify(UUID, on_data)
        await asyncio.sleep(60)

asyncio.run(main())