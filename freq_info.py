"""
Small value object describing a frequency plan:
region index, start frequency (MHz), channel step (kHz), channel count.
"""
import math


class FreqInfo:
    """
    Frequency information.

    region: region index. 0=China-1, 1=China-2, 2=FCC, 3=Japan,
            4=Malaysia, 5=ETSI
    start_freq: start frequency in MHz, range 840~960
    step_freq: channel step in kHz, range 0~500
    count: channel count, range 1~50 (inclusive)
    """

    def __init__(self):
        self._region = 0
        self._start_freq1 = 0  # integer part, MHz
        self._start_freq2 = 0  # fractional part (thousandths of MHz)
        self._step_freq = 0
        self._cnt = 0

    @property
    def region(self) -> int:
        return self._region

    @region.setter
    def region(self, value: int):
        self._region = value

    @property
    def start_freq(self) -> float:
        return self._start_freq1 + self._start_freq2 / 1000.0

    @start_freq.setter
    def start_freq(self, value: float):
        self._start_freq1 = int(math.trunc(value))
        self._start_freq2 = int(math.trunc((value - self._start_freq1) * 1000))

    @property
    def step_freq(self) -> int:
        return self._step_freq

    @step_freq.setter
    def step_freq(self, value: int):
        self._step_freq = value

    @property
    def count(self) -> int:
        return self._cnt

    @count.setter
    def count(self, value: int):
        self._cnt = value
