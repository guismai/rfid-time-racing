"""
TagInfo and Devicepara interop structures, plus TagItem / ShowTagItem
value objects used to track individual tag reads.

TagInfo and Devicepara are ctypes.Structure subclasses so they can
be passed by reference to the native UHFPrimeReader.dll with the
expected memory layout (packed, sequential field order).
"""
import ctypes


class TagInfo(ctypes.Structure):
    """Raw tag data as returned by the reader DLL (GetTagUii)."""

    _pack_ = 1
    _fields_ = [
        ("no", ctypes.c_uint16),        # tag sequence number
        ("rssi", ctypes.c_int16),       # RSSI, unit 0.1 dBm
        ("ant", ctypes.c_uint8),        # antenna index
        ("channel", ctypes.c_uint8),    # channel
        ("crc", ctypes.c_uint8 * 2),    # CRC
        ("pc", ctypes.c_uint8 * 2),     # PC / encoded length + header
        ("len", ctypes.c_uint8),        # valid length of `code`
        ("code", ctypes.c_uint8 * 255),  # tag response data
    ]

    @property
    def NO(self) -> int:
        return self.no

    @property
    def PC(self) -> bytes:
        return bytes(self.pc)

    @property
    def CodeLength(self) -> int:
        return self.len

    @property
    def Code(self) -> bytes:
        return bytes(self.code)

    @property
    def Rssi(self) -> int:
        return self.rssi

    @property
    def Antenna(self) -> int:
        return self.ant

    @property
    def Channel(self) -> int:
        return self.channel

    @property
    def CRC(self) -> bytes:
        return bytes(self.crc)


class Devicepara(ctypes.Structure):
    """Reader device parameters, exchanged as-is with the native DLL."""

    _pack_ = 1
    _fields_ = [
        ("device_addr", ctypes.c_uint8),
        ("rfid_pro", ctypes.c_uint8),
        ("workmode", ctypes.c_uint8),
        ("interface", ctypes.c_uint8),
        ("baudrate", ctypes.c_uint8),
        ("wgset", ctypes.c_uint8),
        ("ant", ctypes.c_uint8),
        ("region", ctypes.c_uint8),
        ("start_freq_i", ctypes.c_uint16),  # big-endian-swapped on the wire, see StartFreq property
        ("start_freq_d", ctypes.c_uint16),
        ("step_freq", ctypes.c_uint16),
        ("cn", ctypes.c_uint8),
        ("rfid_power", ctypes.c_uint8),
        ("inventory_area", ctypes.c_uint8),
        ("q_value", ctypes.c_uint8),
        ("session", ctypes.c_uint8),
        ("acs_addr", ctypes.c_uint8),
        ("acs_data_len", ctypes.c_uint8),
        ("filter_time", ctypes.c_uint8),
        ("triggle_time", ctypes.c_uint8),
        ("buzzer_time", ctypes.c_uint8),
        ("internel_time", ctypes.c_uint8),
    ]

    # --- simple passthrough fields ---
    @property
    def Addr(self) -> int:
        return self.device_addr

    @Addr.setter
    def Addr(self, value: int):
        self.device_addr = value

    @property
    def Protocol(self) -> int:
        return self.rfid_pro

    @Protocol.setter
    def Protocol(self, value: int):
        self.rfid_pro = value

    @property
    def Baud(self) -> int:
        return self.baudrate

    @Baud.setter
    def Baud(self, value: int):
        self.baudrate = value

    @property
    def Workmode(self) -> int:
        return self.workmode

    @Workmode.setter
    def Workmode(self, value: int):
        self.workmode = value

    @property
    def port(self) -> int:
        return self.interface

    @port.setter
    def port(self, value: int):
        self.interface = value

    @property
    def wieggand(self) -> int:
        return self.wgset

    @wieggand.setter
    def wieggand(self, value: int):
        self.wgset = value

    @property
    def Ant(self) -> int:
        return self.ant

    @Ant.setter
    def Ant(self, value: int):
        self.ant = value

    @property
    def Region(self) -> int:
        return self.region

    @Region.setter
    def Region(self, value: int):
        self.region = value

    @property
    def Channel(self) -> int:
        return self.cn

    @Channel.setter
    def Channel(self, value: int):
        self.cn = value

    @property
    def Power(self) -> int:
        return self.rfid_power

    @Power.setter
    def Power(self, value: int):
        self.rfid_power = value

    @property
    def Area(self) -> int:
        return self.inventory_area

    @Area.setter
    def Area(self, value: int):
        self.inventory_area = value

    @property
    def Q(self) -> int:
        return self.q_value

    @Q.setter
    def Q(self, value: int):
        self.q_value = value

    @property
    def Session(self) -> int:
        return self.session

    @Session.setter
    def Session(self, value: int):
        self.session = value

    @property
    def Startaddr(self) -> int:
        return self.acs_addr

    @Startaddr.setter
    def Startaddr(self, value: int):
        self.acs_addr = value

    @property
    def DataLen(self) -> int:
        return self.acs_data_len

    @DataLen.setter
    def DataLen(self, value: int):
        self.acs_data_len = value

    @property
    def Filtertime(self) -> int:
        return self.filter_time

    @Filtertime.setter
    def Filtertime(self, value: int):
        self.filter_time = value

    @property
    def Triggletime(self) -> int:
        return self.triggle_time

    @Triggletime.setter
    def Triggletime(self, value: int):
        self.triggle_time = value

    @property
    def Buzzertime(self) -> int:
        return self.buzzer_time

    @Buzzertime.setter
    def Buzzertime(self, value: int):
        self.buzzer_time = value

    @property
    def IntenelTime(self) -> int:
        return self.internel_time

    @IntenelTime.setter
    def IntenelTime(self, value: int):
        self.internel_time = value

    # --- fields that are byte-swapped on the wire in the original VB code ---
    @staticmethod
    def _swap16(value: int) -> int:
        value &= 0xFFFF
        return ((value >> 8) | (value << 8)) & 0xFFFF

    @property
    def StartFreq(self) -> int:
        return self._swap16(self.start_freq_i)

    @StartFreq.setter
    def StartFreq(self, value: int):
        self.start_freq_i = self._swap16(value)

    @property
    def StartFreqde(self) -> int:
        return self._swap16(self.start_freq_d)

    @StartFreqde.setter
    def StartFreqde(self, value: int):
        self.start_freq_d = self._swap16(value)

    @property
    def Stepfreq(self) -> int:
        return self._swap16(self.step_freq)

    @Stepfreq.setter
    def Stepfreq(self, value: int):
        self.step_freq = self._swap16(value)


class TagItem:
    """A single inventoried tag reading."""

    def __init__(self, no=None, pc=None, code_len=None, code=None, rssi=None,
                 ant=None, channel=None, crc=None, length=None, info: TagInfo = None):
        if info is not None:
            # Constructed straight from the native TagInfo struct (Reader.GetTagUii)
            code_len = info.CodeLength
            if code_len > 0 and info.Code is None:
                raise ValueError("code")
            raw_code = info.Code
            if code_len > len(raw_code):
                raise ValueError("codelen is over")
            self.no = 0  # not copied in the original VB constructor either
            self.pc = bytes()  # not copied in the original VB constructor either
            self.len = info.CodeLength
            self.rssi = info.Rssi
            self.ant = info.Antenna
            self.channel = info.Channel
            self.crc = bytes()  # not copied in the original VB constructor either
            self.code = bytes(raw_code[:code_len]) if code_len > 0 else bytes()
            return

        if pc is None:
            raise ValueError("pc")
        if crc is None:
            raise ValueError("crc")
        if len(pc) != 2:
            raise ValueError("PC must be 2 Byte")
        if code_len and code_len > 0 and code is None:
            raise ValueError("code")
        if code_len and code is not None and code_len > len(code):
            raise ValueError("codelen is over")

        self.no = no
        self.pc = bytes(pc)
        self.rssi = rssi
        self.ant = ant
        self.channel = channel
        self.crc = bytes(crc)
        self.len = length
        if code_len and code_len > 0:
            self.code = bytes(code[:code_len])
        else:
            self.code = bytes()

    @property
    def NO(self):
        return self.no

    @property
    def PC(self):
        return self.pc

    @property
    def Code(self):
        return self.code

    @property
    def Rssi(self):
        return self.rssi

    @property
    def Antenna(self):
        return self.ant

    @property
    def Channel(self):
        return self.channel

    @property
    def CRC(self):
        return self.crc

    @property
    def LEN(self):
        return self.len

    def __eq__(self, other):
        if not isinstance(other, TagItem):
            return False
        return self.crc == other.crc and self.code == other.code and self.pc == other.pc

    def __hash__(self):
        # matches the spirit of the original (order-sensitive rolling hash), simplified
        h = 0
        for data in (self.pc, self.crc, self.code):
            for _ in data:
                h = ((h << 1) + 1) if (h & 0x80000000) else (h << 1)
        return h & 0xFFFFFFFF


class ShowTagItem:
    """
    An aggregated tag row for display: tracks how many times a given
    tag (identified by its code) has been seen per antenna (1..4).
    """

    def __init__(self, item: TagItem = None, no=None, pc=None, code=None,
                 rssi=None, ant=None, channel=None, crc=None, length=None):
        if item is not None:
            if item.Code is None:
                raise ValueError("item.Code")
            if item.Antenna == 0 or item.Antenna > 4:
                raise ValueError("item.Antenna")
            self.counts = [0, 0, 0, 0]
            self.counts[item.Antenna - 1] = 1
            self.no = item.NO
            self.pc = item.PC
            self.code = item.Code
            self.rssi = item.Rssi
            self.channel = item.Channel
            self.crc = item.CRC
            self.len = item.LEN
            return

        if pc is None:
            raise ValueError("pc")
        if len(pc) != 2:
            raise ValueError("PC must be 2 Byte")
        if code is None:
            raise ValueError("code")
        if ant == 0 or ant > 4:
            raise ValueError("channel")

        self.counts = [0, 0, 0, 0]
        self.counts[ant - 1] = 1
        self.no = no
        self.rssi = rssi
        self.channel = channel
        self.crc = crc
        self.code = code
        self.len = length

    @property
    def NO(self):
        return self.no

    @property
    def PC(self):
        return self.pc

    @property
    def Code(self):
        return self.code

    @property
    def Rssi(self):
        return self.rssi

    @property
    def Channel(self):
        return self.channel

    @property
    def CRC(self):
        return self.crc

    @property
    def LEN(self):
        return self.len

    @property
    def Counts(self):
        return self.counts

    def counts_to_string(self) -> str:
        return "/".join(str(c) for c in self.counts)

    def inc_count(self, item: TagItem):
        if 0 < item.Antenna <= 4:
            self.counts[item.Antenna - 1] += 1
        self.no = item.NO
        self.pc = item.PC
        self.code = item.Code
        self.rssi = item.Rssi
        self.channel = item.Channel
        self.crc = item.CRC
        self.len = item.LEN

    def compare_code(self, code: bytes) -> bool:
        if code is None:
            return False
        return self.code == code

    def __str__(self):
        from util import hex_array_to_string
        return hex_array_to_string(self.code)
