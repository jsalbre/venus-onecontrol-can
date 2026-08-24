"""Shared enums and value types for the IDS-CAN protocol layer.

See ARCHITECTURE.md for the full protocol reference these are
transcribed from. Values are unvalidated against real hardware until
Phase 0/1 acceptance criteria are met (see TODO.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class MessageType(IntEnum):
    """CAN ID message type. Broadcast types use 11-bit IDs; point-to-point
    types (value >= 0x80) use 29-bit extended IDs."""

    NETWORK = 0
    CIRCUIT_ID = 1
    DEVICE_ID = 2
    DEVICE_STATUS = 3
    PRODUCT_STATUS = 6
    TIME = 7

    REQUEST = 128
    RESPONSE = 129
    COMMAND = 130
    EXT_STATUS = 131
    TEXT_CONSOLE = 132
    GROUP_ID = 133

    @property
    def is_point_to_point(self) -> bool:
        return self.value >= 0x80


class DeviceType(IntEnum):
    """DEVICE_TYPE enum from DEVICE_ID broadcasts, per decompiled Lippert
    firmware (D-Jeffrey/UnityX-canbus IDS-coding.md)."""

    UNKNOWN = 0
    GENERIC = 1
    TABLET = 2
    LATCHING_RELAY = 3
    MOMENTARY_RELAY = 4
    LATCHING_H_BRIDGE = 5
    MOMENTARY_H_BRIDGE = 6
    LEVELER_TYPE_1 = 7
    SWITCH = 8
    TOUCHSCREEN_SWITCH = 9
    TANK_SENSOR = 10
    LEVELER_TYPE_2 = 11
    HOUR_METER = 12
    RGB_LIGHT = 13
    REAL_TIME_CLOCK = 14
    IR_REMOTE_CONTROL = 15
    HVAC_CONTROL = 16
    LEVELER_TYPE_3 = 17
    CAN_TO_ETHERNET_GATEWAY = 18
    IN_TRANSIT_POWER_DISCONNECT = 19
    DIMMABLE_LIGHT = 20
    ONECONTROL_TOUCH_PAD = 21
    ANDROID_MOBILE_DEVICE = 22
    IOS_MOBILE_DEVICE = 23
    GENERATOR_GENIE = 24
    TEMPERATURE_SENSOR = 25
    AC_POWER_MONITOR = 26
    DC_POWER_MONITOR = 27
    SETEC_POWER_MANAGER = 28
    ONECONTROL_CLOUD_GATEWAY = 29
    LATCHING_RELAY_TYPE_2 = 30
    MOMENTARY_RELAY_TYPE_2 = 31
    LATCHING_H_BRIDGE_TYPE_2 = 32
    MOMENTARY_H_BRIDGE_TYPE_2 = 33
    ONECONTROL_APPLICATION = 34
    CONFIGURATOR_APPLICATION = 35
    BLUETOOTH_GATEWAY = 36
    MAXX_FAN = 37
    RAIN_SENSOR = 38
    CHASSIS_INFO = 39
    LEVELER_TYPE_4 = 40
    AWNING_SENSOR = 47


# DEVICE_TYPEs that share the RELAY_TYPE_2_STATUS_PARAMS 6-byte DEVICE_STATUS
# layout (latching relays used for lights/pump/water-heater, and H-bridge
# motors used for awnings/slides/jacks). The command path treats these very
# differently -- see command.py -- but the STATUS decode is identical.
RELAY_OR_MOTOR_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_RELAY,
        DeviceType.MOMENTARY_RELAY,
        DeviceType.LATCHING_H_BRIDGE,
        DeviceType.MOMENTARY_H_BRIDGE,
        DeviceType.LATCHING_RELAY_TYPE_2,
        DeviceType.MOMENTARY_RELAY_TYPE_2,
        DeviceType.LATCHING_H_BRIDGE_TYPE_2,
        DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
    }
)

# DEVICE_TYPEs that are known to have a motor (never a valid COMMAND target
# in this project -- see ARCHITECTURE.md's "No Motor Commands" boundary).
MOTOR_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_H_BRIDGE,
        DeviceType.MOMENTARY_H_BRIDGE,
        DeviceType.LATCHING_H_BRIDGE_TYPE_2,
        DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
    }
)

# DEVICE_TYPEs allowed as COMMAND targets in v1 (lights, relays, pump,
# water heater -- never anything in MOTOR_DEVICE_TYPES).
COMMANDABLE_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_RELAY,
        DeviceType.MOMENTARY_RELAY,
        DeviceType.LATCHING_RELAY_TYPE_2,
        DeviceType.MOMENTARY_RELAY_TYPE_2,
        DeviceType.DIMMABLE_LIGHT,
    }
)

# The real FUNCTION_NAME -> display-name table, extracted from the
# LippertConnect Android app's decompiled FUNCTION_NAME class
# (assembly_0080, static constructor). Names are NEVER transmitted over the
# CAN bus -- only the numeric FUNCTION_NAME code is broadcast; the app (and
# now this project) resolves it locally against this same fixed table.
# Confirmed complete: values 0-445, no gaps, no duplicate keys. Two distinct
# codes (109, 142) both display as "Leveler" -- not a transcription error,
# the vendor table genuinely has two entries with the same name.
FUNCTION_NAMES: dict[int, str] = {
    0: "UNKNOWN",
    1: "Diagnostic Tool",
    2: "MyRV Tablet",
    3: "Gas Water Heater",
    4: "Electric Water Heater",
    5: "Water Pump",
    6: "Bath Vent",
    7: "Light",
    8: "Flood Light",
    9: "Work Light",
    10: "Front Bedroom Ceiling Light",
    11: "Front Bedroom Overhead Light",
    12: "Front Bedroom Vanity Light",
    13: "Front Bedroom Sconce Light",
    14: "Front Bedroom Loft Light",
    15: "Rear Bedroom Ceiling Light",
    16: "Rear Bedroom Overhead Light",
    17: "Rear Bedroom Vanity Light",
    18: "Rear Bedroom Sconce Light",
    19: "Rear Bedroom Loft Light",
    20: "Loft Light",
    21: "Front Hall Light",
    22: "Rear Hall Light",
    23: "Front Bathroom Light",
    24: "Front Bathroom Vanity Light",
    25: "Front Bathroom Ceiling Light",
    26: "Front Bathroom Shower Light",
    27: "Front Bathroom Sconce Light",
    28: "Rear Bathroom Vanity Light",
    29: "Rear Bathroom Ceiling Light",
    30: "Rear Bathroom Shower Light",
    31: "Rear Bathroom Sconce Light",
    32: "Kitchen Ceiling Light",
    33: "Kitchen Sconce Light",
    34: "Kitchen Pendants Light",
    35: "Kitchen Range Light",
    36: "Kitchen Counter Light",
    37: "Kitchen Bar Light",
    38: "Kitchen Island Light",
    39: "Kitchen Chandelier Light",
    40: "Kitchen Under Cabinet Light",
    41: "Living Room Ceiling Light",
    42: "Living Room Sconce Light",
    43: "Living Room Pendants Light",
    44: "Living Room Bar Light",
    45: "Garage Ceiling Light",
    46: "Garage Cabinet Light",
    47: "Security Light",
    48: "Porch Light",
    49: "Awning Light",
    50: "Bathroom Light",
    51: "Bathroom Vanity Light",
    52: "Bathroom Ceiling Light",
    53: "Bathroom Shower Light",
    54: "Bathroom Sconce Light",
    55: "Hall Light",
    56: "Bunk Room Light",
    57: "Bedroom Light",
    58: "Living Room Light",
    59: "Kitchen Light",
    60: "Lounge Light",
    61: "Ceiling Light",
    62: "Entry Light",
    63: "Bed Ceiling Light",
    64: "Bedroom Lav Light",
    65: "Shower Light",
    66: "Galley Light",
    67: "Fresh Tank",
    68: "Grey Tank",
    69: "Black Tank",
    70: "Fuel Tank",
    71: "Generator Fuel Tank",
    72: "Auxiliary Fuel Tank",
    73: "Front Bath Grey Tank",
    74: "Front Bath Fresh Tank",
    75: "Front Bath Black Tank",
    76: "Rear Bath Grey Tank",
    77: "Rear Bath Fresh Tank",
    78: "Rear Bath Black Tank",
    79: "Main Bath Grey Tank",
    80: "Main Bath Fresh Tank",
    81: "Main Bath Black Tank",
    82: "Galley Grey Tank",
    83: "Galley Fresh Tank",
    84: "Galley Black Tank",
    85: "Kitchen Grey Tank",
    86: "Kitchen Fresh Tank",
    87: "Kitchen Black Tank",
    88: "Landing Gear",
    89: "Front Stabilizer",
    90: "Rear Stabilizer",
    91: "TV Lift",
    92: "Bed Lift",
    93: "Bath Vent Cover",
    94: "Door Lock",
    95: "Generator",
    96: "Slide",
    97: "Main Slide",
    98: "Bedroom Slide",
    99: "Galley Slide",
    100: "Kitchen Slide",
    101: "Closet Slide",
    102: "Optional Slide",
    103: "Door Side Slide",
    104: "Off-Door Slide",
    105: "Awning",
    106: "Level Up Leveler",
    107: "Water Tank Heater",
    108: "MyRV Touchscreen",
    109: "Leveler",
    110: "Vent Cover",
    111: "Front Bedroom Vent Cover",
    112: "Bedroom Vent Cover",
    113: "Front Bath Vent Cover",
    114: "Main Bath Vent Cover",
    115: "Rear Bath Vent Cover",
    116: "Kitchen Vent Cover",
    117: "Living Room Vent Cover",
    118: "4-Leg Truck Camper Leveler",
    119: "6-Leg Hall Effect EJ Leveler",
    120: "Patio Light",
    121: "Hutch Light",
    122: "Scare Light",
    123: "Dinette Light",
    124: "Bar Light",
    125: "Overhead Light",
    126: "Overhead Bar Light",
    127: "Foyer Light",
    128: "Ramp Door Light",
    129: "Entertainment Light",
    130: "Rear Entry Door Light",
    131: "Ceiling Fan Light",
    132: "Overhead Fan Light",
    133: "Bunk Slide",
    134: "Bed Slide",
    135: "Wardrobe Slide",
    136: "Entertainment Slide",
    137: "Sofa Slide",
    138: "Patio Awning",
    139: "Rear Awning",
    140: "Side Awning",
    141: "Jacks",
    142: "Leveler",
    143: "Exterior Light",
    144: "Lower Accent Light",
    145: "Upper Accent Light",
    146: "DS Security Light",
    147: "ODS Security Light",
    148: "Slide In Slide",
    149: "Hitch Light",
    150: "Clock",
    151: "TV",
    152: "DVD",
    153: "Blu-ray",
    154: "VCR",
    155: "PVR",
    156: "Cable",
    157: "Satellite",
    158: "Audio",
    159: "CD Player",
    160: "Tuner",
    161: "Radio",
    162: "Speakers",
    163: "Game",
    164: "Clock Radio",
    165: "Aux",
    166: "Climate Zone",
    167: "Fireplace",
    168: "Thermostat",
    169: "Front Cap Light",
    170: "Step Light",
    171: "DS Flood Light",
    172: "Interior Light",
    173: "Fresh Tank Heater",
    174: "Grey Tank Heater",
    175: "Black Tank Heater",
    176: "LP Tank",
    177: "Stall Light",
    178: "Main Light",
    179: "Bath Light",
    180: "Bunk Light",
    181: "Bed Light",
    182: "Cabinet Light",
    183: "Network Bridge",
    184: "Ethernet Bridge",
    185: "WiFi Bridge",
    186: "In Transit Power Disconnect",
    187: "Level Up Unity",
    188: "TT Leveler",
    189: "Travel Trailer Leveler",
    190: "Fifth Wheel Leveler",
    191: "Fuel Pump",
    192: "Main Climate Zone",
    193: "Bedroom Climate Zone",
    194: "Garage Climate Zone",
    195: "Compartment Light",
    196: "Trunk Light",
    197: "Bar TV",
    198: "Bathroom TV",
    199: "Bedroom TV",
    200: "Bunk Room TV",
    201: "Exterior TV",
    202: "Front Bathroom TV",
    203: "Front Bedroom TV",
    204: "Garage TV",
    205: "Kitchen TV",
    206: "Living Room TV",
    207: "Loft TV",
    208: "Lounge TV",
    209: "Main TV",
    210: "Patio TV",
    211: "Rear Bathroom TV",
    212: "Rear Bedroom TV",
    213: "Bathroom Door Lock",
    214: "Bedroom Door Lock",
    215: "Front Door Lock",
    216: "Garage Door Lock",
    217: "Main Door Lock",
    218: "Patio Door Lock",
    219: "Rear Door Lock",
    220: "Accent Light",
    221: "Bathroom Accent Light",
    222: "Bedroom Accent Light",
    223: "Front Bedroom Accent Light",
    224: "Garage Accent Light",
    225: "Kitchen Accent Light",
    226: "Patio Accent Light",
    227: "Rear Bedroom Accent Light",
    228: "Bedroom Radio",
    229: "Bunk Room Radio",
    230: "Exterior Radio",
    231: "Front Bedroom Radio",
    232: "Garage Radio",
    233: "Kitchen Radio",
    234: "Living Room Radio",
    235: "Loft Radio",
    236: "Patio Radio",
    237: "Rear Bedroom Radio",
    238: "Bedroom Entertainment System",
    239: "Bunk Room Entertainment System",
    240: "Entertainment System",
    241: "Exterior Entertainment System",
    242: "Front Bedroom Entertainment System",
    243: "Garage Entertainment System",
    244: "Kitchen Entertainment System",
    245: "Living Room Entertainment System",
    246: "Loft Entertainment System",
    247: "Main Entertainment System",
    248: "Patio Entertainment System",
    249: "Rear Bedroom Entertainment System",
    250: "Left Stabilizer",
    251: "Right Stabilizer",
    252: "Stabilizer",
    253: "Solar",
    254: "Solar Power",
    255: "Battery",
    256: "Main Battery",
    257: "Aux Battery",
    258: "Shore Power",
    259: "AC Power",
    260: "AC Mains",
    261: "Aux Power",
    262: "Outputs",
    263: "Ramp Door",
    264: "Fan",
    265: "Bath Fan",
    266: "Rear Fan",
    267: "Front Fan",
    268: "Kitchen Fan",
    269: "Ceiling Fan",
    270: "Tank Heater",
    271: "Front Ceiling Light",
    272: "Rear Ceiling Light",
    273: "Cargo Light",
    274: "Fascia Light",
    275: "Slide Ceiling Light",
    276: "Slide Overhead Light",
    277: "Décor Light",
    278: "Reading Light",
    279: "Front Reading Light",
    280: "Rear Reading Light",
    281: "Living Room Climate Zone",
    282: "Front Living Room Climate Zone",
    283: "Rear Living Room Climate Zone",
    284: "Front Bedroom Climate Zone",
    285: "Rear Bedroom Climate Zone",
    286: "Bed Tilt",
    287: "Front Bed Tilt",
    288: "Rear Bed Tilt",
    289: "Men's Light",
    290: "Women's Light",
    291: "Service Light",
    292: "ODS Flood Light",
    293: "Underbody Accent Light",
    294: "Speaker Light",
    295: "Water Heater",
    296: "Water Heaters",
    297: "AquaFi",
    298: "ConnectAnywhere",
    299: "Slide {0} (if equip)",
    300: "Awning {0} (if equip)",
    301: "Awning Light {0} (if equip)",
    302: "Interior Light {0} (if equip)",
    303: "Waste valve",
    304: "Tire Linc",
    305: "Front Locker Light",
    306: "Rear Locker Light",
    307: "Rear Aux Power",
    308: "Rock Light",
    309: "Chassis Light",
    310: "Exterior Shower Light",
    311: "Living Room Accent Light",
    312: "Rear Flood Light",
    313: "Passenger Flood Light",
    314: "Driver Flood Light",
    315: "Bathroom Slide",
    316: "Roof Lift",
    317: "Yeti Package",
    318: "Propane Locker",
    319: "Garage Awning",
    320: "Monitor Panel",
    321: "Camera",
    322: "Jayco Aus TBB GW",
    323: "GateWay RvLink",
    324: "Accessory Temperature",
    325: "Accessory Refrigerator",
    326: "Accessory Fridge",
    327: "Accessory Freezer",
    328: "Accessory External",
    329: "Trailer Brake Controller",
    330: "Refrigerator Temperature",
    331: "Refrigerator Temperature Home",
    332: "Freezer Temperature",
    333: "Freezer Temperature Home",
    334: "Cooler Temperature",
    335: "Kitchen Temperature",
    336: "Living Room Temperature",
    337: "Bedroom Temperature",
    338: "Master Bedroom Temperature",
    339: "Garage Temperature",
    340: "Basement Temperature",
    341: "Bathroom Temperature",
    342: "Storage Area Temperature",
    343: "Drivers Area Temperature",
    344: "Bunks Temperature",
    345: "RV Tank",
    346: "Home Tank",
    347: "Cabin Tank",
    348: "BBQ Tank",
    349: "Grill Tank",
    350: "Submarine Tank",
    351: "Other Tank",
    352: "Anti-Lock Braking System",
    353: "LoCAP Gateway",
    354: "BootLoader",
    355: "Auxiliary Battery",
    356: "Chassis Battery",
    357: "House Battery",
    358: "Kitchen Battery",
    359: "Electronic Sway Control",
    360: "Jack Lights",
    361: "Awning Sensor",
    362: "Interior Step Light",
    363: "Exterior Step Light",
    364: "Wifi Booster",
    365: "Audible Alert",
    366: "Soffit Light",
    367: "Battery Bank",
    368: "RV Battery",
    369: "Solar Battery",
    370: "Tongue Battery",
    371: "Brake Controller Axle 1",
    372: "Brake Controller Axle 2",
    373: "Brake Controller Axle 3",
    374: "Lead-Acid",
    375: "Liquid Lead-Acid",
    376: "Gel Lead-Acid",
    377: "AGM - Absorbent Glass Mat",
    378: "Lithium",
    379: "Front Awning",
    380: "Dinette Slide",
    381: "Holding Tanks Heater",
    382: "Inverter",
    383: "Battery Heat",
    384: "Camera Power",
    385: "Patio Awning Light",
    386: "Garage Awning Light",
    387: "Rear Awning Light",
    388: "Side Awning Light",
    389: "Slide Awning Light",
    390: "Slide Awning",
    391: "Front Awning Light",
    392: "Central Lights",
    393: "Right Side Lights",
    394: "Left Side Lights",
    395: "Right Scene Lights",
    396: "Left Scene Lights",
    397: "Rear Scene Lights",
    398: "Computer Fan",
    399: "Battery Fan",
    400: "Right Slide Room",
    401: "Left Slide Room",
    402: "Dump Light",
    403: "Base Camp Touchscreen",
    404: "Base Camp Leveler",
    405: "Refrigerator",
    406: "Kitchen Pendant Light",
    407: "Door Side Sofa Slide",
    408: "Off Door Side Sofa Slide",
    409: "Rear Bed Slide",
    410: "Theater Lights",
    411: "Utility Cabinet Light",
    412: "Chase Light",
    413: "Floor Lights",
    414: "Roof Top Tent Light",
    415: "Upper Power Shades",
    416: "Lower Power Shades",
    417: "Living Room Power Shades",
    418: "Bedroom Power Shades",
    419: "Bathroom Power Shades",
    420: "Bunk Power Shades",
    421: "Loft Power Shades",
    422: "Front Power Shades",
    423: "Rear Power Shades",
    424: "Main Power Shades",
    425: "Garage Power Shades",
    426: "Door Side Power Shades",
    427: "Off Door Side Power Shades",
    428: "Fresh Tank Valve",
    429: "Grey Tank Valve",
    430: "Black Tank Valve",
    431: "Front Bath Grey Tank Valve",
    432: "Front Bath Fresh Tank Valve",
    433: "Front Bath Black Tank Valve",
    434: "Rear Bath Grey Tank Valve",
    435: "Rear Bath Fresh Tank Valve",
    436: "Rear Bath Black Tank Valve",
    437: "Main Bath Grey Tank Valve",
    438: "Main Bath Fresh Tank Valve",
    439: "Main Bath Black Tank Valve",
    440: "Galley Grey Tank Valve",
    441: "Galley Fresh Tank Valve",
    442: "Galley Black Tank Valve",
    443: "Kitchen Grey Tank Valve",
    444: "Kitchen Fresh Tank Valve",
    445: "Kitchen Black Tank Valve",
}


def function_name_label(value: int) -> str:
    """Human-readable label for a FUNCTION_NAME value, for logging only."""
    return FUNCTION_NAMES.get(value, f"UNKNOWN_{value}")


def search_function_names(query: str) -> list[tuple[int, str]]:
    """Case-insensitive substring search against FUNCTION_NAMES, sorted by
    name. For interactive tools (manage-system) that need to pick a
    FUNCTION_NAME by searching rather than already knowing its numeric
    code."""
    query_lower = query.lower()
    matches = [(value, name) for value, name in FUNCTION_NAMES.items() if query_lower in name.lower()]
    return sorted(matches, key=lambda item: item[1])


@dataclass(frozen=True)
class StableKey:
    """Identifies a physical device independent of its volatile CAN
    SourceAddress. See ARCHITECTURE.md's "Stable-Key Device Discovery".

    kind="function_name": primary=FUNCTION_NAME, instance=function_instance nibble.
    kind="product_id": primary=PRODUCT_ID, instance=product instance (fallback,
        used only when FUNCTION_NAME is 0/unset on a device).
    """

    kind: str
    primary: int
    instance: int

    def __post_init__(self) -> None:
        if self.kind not in ("function_name", "product_id"):
            raise ValueError(f"invalid StableKey kind: {self.kind!r}")

    def to_config_string(self) -> str:
        return f"{self.kind}={self.primary},{'function_instance' if self.kind == 'function_name' else 'instance'}={self.instance}"

    @classmethod
    def from_config_string(cls, text: str) -> "StableKey":
        parts = dict(item.split("=", 1) for item in text.split(","))
        if "function_name" in parts:
            return cls("function_name", int(parts["function_name"]), int(parts["function_instance"]))
        if "product_id" in parts:
            return cls("product_id", int(parts["product_id"]), int(parts["instance"]))
        raise ValueError(f"unrecognized stable key format: {text!r}")
