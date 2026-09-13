"""
Regional channel plans for the reader.

Defines the radio regulation regions supported by the reader, plus
helper classes that describe each region's channel plan (start
frequency, step, channel count) and provide convenience lists of
channel items to fill combo boxes with.
"""
from enum import IntEnum


class ChannelRegion(IntEnum):
    Custom = 0x0
    USA = 1
    Korea = 2
    Europe = 3
    Japan = 4
    Malaysia = 5
    Europe3 = 6
    China_1 = 7
    China_2 = 8


class ChannelItem:
    """A single selectable channel frequency (used to populate a start-frequency list)."""

    EmptyArray = []

    def __init__(self, freq: float):
        self._freq = freq

    @property
    def freq(self) -> float:
        return self._freq

    def __str__(self):
        return f"{self._freq:.3f}"

    def __repr__(self):
        return self.__str__()


class ChannelCount:
    """A selectable channel count entry (used to populate an end-frequency list)."""

    EmptyArray = []

    def __init__(self, count: int, region: "ChannelRegionItem"):
        self._count = count
        self._region = region

    @property
    def count(self) -> int:
        return self._count

    @count.setter
    def count(self, value: int):
        self._count = value

    def __str__(self):
        if self._region.value == ChannelRegion.Custom:
            return str(self._count)
        return f"{self._region.start_freq + (self._count - 1) * self._region.freq_step / 1000.0:.3f}"

    def __repr__(self):
        return self.__str__()


class ChannelRegionItem:
    """Describes a region's frequency plan: start frequency, channel step (kHz), channel count."""

    def __init__(self, value: ChannelRegion, freq_start: float, freq_step: int, freq_count: int):
        self._value = value
        self._f_freq_start = freq_start
        self._i_freq_step = freq_step
        self._i_freq_count = freq_count

    @property
    def value(self) -> ChannelRegion:
        return self._value

    @property
    def start_freq(self) -> float:
        return self._f_freq_start

    @property
    def freq_step(self) -> int:
        return self._i_freq_step

    def get_channel_items(self):
        """Returns the list of ChannelItem (start-frequency choices) for this region."""
        if self._i_freq_count == 0:
            return list(ChannelItem.EmptyArray)
        freq = self._f_freq_start
        items = []
        for _ in range(self._i_freq_count):
            items.append(ChannelItem(freq))
            freq += self._i_freq_step / 1000.0
        return items

    def get_channel_counts(self):
        """Returns the list of ChannelCount (end-frequency/channel-count choices) for this region."""
        if self._i_freq_count == 0:
            return list(ChannelCount.EmptyArray)
        return [ChannelCount(i + 1, self) for i in range(self._i_freq_count)]

    def __str__(self):
        return ChannelRegionItem.region_to_string(self._value)

    @staticmethod
    def region_to_string(value: ChannelRegion) -> str:
        return {
            ChannelRegion.Korea: "Korea",
            ChannelRegion.Europe: "Europe",
            ChannelRegion.China_1: "China_1",
            ChannelRegion.China_2: "China_2",
            ChannelRegion.USA: "USA",
            ChannelRegion.Japan: "Japan",
            ChannelRegion.Malaysia: "Malaysia",
            ChannelRegion.Europe3: "Europe3",
            ChannelRegion.Custom: "Custom",
        }.get(value, f"Undefined value: 0x{int(value):02X}")

    @staticmethod
    def string_to_region(value: str) -> ChannelRegion:
        return {
            "Korea": ChannelRegion.Korea,
            "Europe": ChannelRegion.Europe,
            "China_1": ChannelRegion.China_1,
            "China_2": ChannelRegion.China_2,
            "USA": ChannelRegion.USA,
            "Japan": ChannelRegion.Japan,
            "Malaysia": ChannelRegion.Malaysia,
            "Europe3": ChannelRegion.Europe3,
            "Custom": ChannelRegion.Custom,
        }.get(value, ChannelRegion.USA)

    @staticmethod
    def option_from_value(region: ChannelRegion, b_standard: bool):
        items = ChannelRegionItem.OptionsStandard if b_standard else ChannelRegionItem.Options
        for item in items:
            if item.value == region:
                return item
        return None


# Predefined regions (values taken from the original VB source)
ChannelRegionItem.USARegion = ChannelRegionItem(ChannelRegion.USA, 902.750, 500, 50)
ChannelRegionItem.KoreaRegion = ChannelRegionItem(ChannelRegion.Korea, 917.100, 200, 32)
ChannelRegionItem.EuropeRegion = ChannelRegionItem(ChannelRegion.Europe, 865.100, 200, 15)
ChannelRegionItem.JapanRegion = ChannelRegionItem(ChannelRegion.Japan, 952.200, 200, 8)
ChannelRegionItem.MalaysiaRegion = ChannelRegionItem(ChannelRegion.Malaysia, 919.500, 500, 7)
ChannelRegionItem.Europe3Region = ChannelRegionItem(ChannelRegion.Europe3, 865.700, 600, 4)
ChannelRegionItem.China1Region = ChannelRegionItem(ChannelRegion.China_1, 840.125, 250, 20)
ChannelRegionItem.CustomRegion = ChannelRegionItem(ChannelRegion.Custom, 0.0, 0, 0)
ChannelRegionItem.China2Region = ChannelRegionItem(ChannelRegion.China_2, 920.125, 250, 20)

ChannelRegionItem.Options = [
    ChannelRegionItem.USARegion,
    ChannelRegionItem.KoreaRegion,
    ChannelRegionItem.EuropeRegion,
    ChannelRegionItem.JapanRegion,
    ChannelRegionItem.MalaysiaRegion,
    ChannelRegionItem.Europe3Region,
    ChannelRegionItem.China1Region,
    ChannelRegionItem.China2Region,
    ChannelRegionItem.CustomRegion,
]
ChannelRegionItem.OptionsStandard = list(ChannelRegionItem.Options)
