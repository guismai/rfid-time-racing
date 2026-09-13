"""
`API` loads UHFPrimeReader.dll via ctypes and declares the native reader
functions (Open/Close/GetDevicePara/SetDevicePara/Inventory/...). This only
works on Windows, with UHFPrimeReader.dll (and its hidapi.dll dependency)
next to the executable or on PATH.

The physical RFID reader this talks to is a Chafon CF561 UHF reader module.

`Reader` is a thin, Pythonic wrapper around those calls.
"""
import ctypes
import os
import platform
import sys
import time

from reader_exception import ReaderException
from tag_item import TagInfo, Devicepara, TagItem

DLL_NAME = "UHFPrimeReader.dll"
HIDAPI_DLL_NAME = "hidapi.dll"

# The two DLLs are bundled with this package (see the `lib/` folder next to
# this file) instead of relying on the system PATH.
# When frozen into a standalone .exe (PyInstaller), bundled data files are
# extracted to sys._MEIPASS at runtime instead of sitting next to this .py
# file, so that path takes priority when present.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(_BASE_DIR, "lib")


class API:
    """Native function bindings for UHFPrimeReader.dll."""

    _dll = None

    @classmethod
    def _lib(cls):
        if cls._dll is None:
            if platform.system() != "Windows":
                raise OSError(
                    f"{DLL_NAME} is a Windows DLL and can only be loaded with ctypes "
                    "on Windows. This module cannot talk to real hardware on this OS."
                )
            dll_path = os.path.join(_LIB_DIR, DLL_NAME)
            if not os.path.isfile(dll_path):
                raise FileNotFoundError(
                    f"{DLL_NAME} not found at {dll_path}. Make sure the bundled "
                    f"'lib' folder ships alongside this script."
                )
            # UHFPrimeReader.dll depends on hidapi.dll: make sure Windows'
            # DLL search finds it in our bundled lib/ folder too, regardless
            # of the current working directory.
            if hasattr(os, "add_dll_directory"):  # Python 3.8+ on Windows
                os.add_dll_directory(_LIB_DIR)
            # Both DLLs shipped here are 32-bit (PE i386) — this process must
            # be running a 32-bit ("x86") Python interpreter for the load
            # below to succeed; a 64-bit interpreter will raise OSError
            # "%1 is not a valid Win32 application" (WinError 193). See
            # arch_check.py for the proactive startup warning.
            #
            # DEBUG SWITCH: set the environment variable UHF_DLL_CDECL=1 to
            # load the DLL as __cdecl instead of the default __stdcall.
            # Some vendor DLLs (targeting VB6/Delphi/C#) export undecorated
            # names while actually being __cdecl underneath; if calls after
            # the first one behave strangely (e.g. GetDevicePara always
            # times out right after a successful OpenHidConnection), that's
            # a classic symptom of a calling-convention mismatch corrupting
            # the stack between calls. Try both and see which one works.
            use_cdecl = os.environ.get("UHF_DLL_CDECL", "0") == "1"
            loader = ctypes.CDLL if use_cdecl else ctypes.WinDLL
            print(f"[DEBUG] Loading {dll_path} via "
                  f"{'ctypes.CDLL (__cdecl)' if use_cdecl else 'ctypes.WinDLL (__stdcall)'}",
                  file=sys.stderr)
            try:
                cls._dll = loader(dll_path)
            except OSError as ex:
                if getattr(ex, "winerror", None) == 193 or "193" in str(ex):
                    from arch_check import get_python_bits
                    raise OSError(
                        f"Impossible de charger UHFPrimeReader.dll (WinError 193) : "
                        f"cette DLL est compilee en 32 bits, mais l'interpreteur "
                        f"Python actuel est en {get_python_bits()} bits. Utilisez un "
                        f"Python 32 bits (x86) pour piloter le lecteur."
                    ) from ex
                raise
            cls._declare_signatures(cls._dll)
        return cls._dll

    @staticmethod
    def _declare_signatures(dll):
        dll.OpenDevice.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_uint8]
        dll.OpenDevice.restype = ctypes.c_int32

        dll.OpenNetConnection.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p,
                                           ctypes.c_uint16, ctypes.c_uint32]
        dll.OpenNetConnection.restype = ctypes.c_int32

        dll.OpenHidConnection.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint16]
        dll.OpenHidConnection.restype = ctypes.c_int32

        dll.CFHid_GetUsbCount.argtypes = []
        dll.CFHid_GetUsbCount.restype = ctypes.c_int32

        dll.CFHid_GetUsbInfo.argtypes = [ctypes.c_uint16, ctypes.POINTER(ctypes.c_uint8)]
        dll.CFHid_GetUsbInfo.restype = ctypes.c_bool

        dll.CloseDevice.argtypes = [ctypes.c_void_p]
        dll.CloseDevice.restype = ctypes.c_int32

        dll.GetDevicePara.argtypes = [ctypes.c_void_p, ctypes.POINTER(Devicepara)]
        dll.GetDevicePara.restype = ctypes.c_int32

        dll.SetDevicePara.argtypes = [ctypes.c_void_p, Devicepara]
        dll.SetDevicePara.restype = ctypes.c_int32

        dll.SetRFPower.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8]
        dll.SetRFPower.restype = ctypes.c_int32

        dll.InventoryContinue.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32]
        dll.InventoryContinue.restype = ctypes.c_int32

        dll.GetTagUii.argtypes = [ctypes.c_void_p, ctypes.POINTER(TagInfo), ctypes.c_uint16]
        dll.GetTagUii.restype = ctypes.c_int32

        dll.InventoryStop.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        dll.InventoryStop.restype = ctypes.c_int32

        dll.Release_Relay.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
        dll.Release_Relay.restype = ctypes.c_int32

        dll.Close_Relay.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
        dll.Close_Relay.restype = ctypes.c_int32

    # --- thin wrappers, one per native call -------------------------------------------------
    @classmethod
    def OpenDevice(cls, port: str, baudrate: int):
        handle = ctypes.c_void_p()
        state = cls._lib().OpenDevice(ctypes.byref(handle), port.encode("ascii"), baudrate)
        return state, handle

    @classmethod
    def OpenNetConnection(cls, ip: str, port: int, timeout_ms: int):
        handle = ctypes.c_void_p()
        state = cls._lib().OpenNetConnection(ctypes.byref(handle), ip.encode("ascii"), port, timeout_ms)
        return state, handle

    @classmethod
    def OpenHidConnection(cls, index: int):
        handle = ctypes.c_void_p()
        state = cls._lib().OpenHidConnection(ctypes.byref(handle), index)
        print(f"[DEBUG] OpenHidConnection(index={index}) -> state={state}, handle={handle.value}",
              file=sys.stderr)
        return state, handle

    @classmethod
    def CFHid_GetUsbCount(cls) -> int:
        return cls._lib().CFHid_GetUsbCount()

    @classmethod
    def CFHid_GetUsbInfo(cls, index: int, buffer: bytearray) -> bool:
        arr = (ctypes.c_uint8 * len(buffer)).from_buffer(buffer)
        return bool(cls._lib().CFHid_GetUsbInfo(index, arr))

    @classmethod
    def CloseDevice(cls, handle) -> int:
        return cls._lib().CloseDevice(handle)

    @classmethod
    def GetDevicePara(cls, handle):
        info = Devicepara()
        state = cls._lib().GetDevicePara(handle, ctypes.byref(info))
        print(f"[DEBUG] GetDevicePara(handle={handle}) -> state={state}", file=sys.stderr)
        return state, info

    @classmethod
    def SetDevicePara(cls, handle, dev_info: Devicepara) -> int:
        return cls._lib().SetDevicePara(handle, dev_info)

    @classmethod
    def SetRFPower(cls, handle, power: int, reserved: int = 0) -> int:
        return cls._lib().SetRFPower(handle, power, reserved)

    @classmethod
    def InventoryContinue(cls, handle, inv_count: int, inv_param: int) -> int:
        return cls._lib().InventoryContinue(handle, inv_count, inv_param)

    @classmethod
    def GetTagUii(cls, handle, timeout: int):
        info = TagInfo()
        state = cls._lib().GetTagUii(handle, ctypes.byref(info), timeout)
        return state, info

    @classmethod
    def InventoryStop(cls, handle, timeout: int) -> int:
        return cls._lib().InventoryStop(handle, timeout)

    @classmethod
    def Release_Relay(cls, handle, time: int) -> int:
        return cls._lib().Release_Relay(handle, time)

    @classmethod
    def Close_Relay(cls, handle, time: int) -> int:
        return cls._lib().Close_Relay(handle, time)


class Reader:
    """High-level reader handle: open/close, get/set device parameters, run inventory, ..."""

    # open state: 0 closed, 1 serial, 2 network, 3 usb/hid
    def __init__(self):
        self._handler = None
        self._state = 0
        self._usbindex = 0
        self._ip = ""
        self._net_port = 0
        self._sport = ""

    @property
    def IsOpened(self) -> bool:
        return self._state != 0

    @property
    def IsOpenedAsCom(self) -> bool:
        return self._state == 1

    @property
    def IsOpenedAsNetwork(self) -> bool:
        return self._state == 2

    @property
    def IPAddress(self) -> str:
        return self._ip

    @property
    def NetPort(self) -> int:
        return self._net_port

    @property
    def PortName(self) -> str:
        return self._sport

    def _reset_handle(self):
        if self._handler:
            try:
                API.CloseDevice(self._handler)
            except Exception:
                pass
            self._handler = None

    def open_serial(self, port: str, baudrate: int):
        """Equivalent of VB `Open(port As String, Baudrate As Byte)`."""
        if port is None:
            raise ValueError("port(Serial)")
        port = port.strip()
        if len(port) == 0:
            raise ValueError("please input serial")
        if self._state != 0:
            raise RuntimeError("reader is already opened")

        self._reset_handle()
        state, handle = API.OpenDevice(port, baudrate)
        if state != ReaderException.ERROR_SUCCESS:
            self._handler = None
            raise IOError(f"serial '{port}' open fail")
        self._handler = handle
        self._sport = port
        self._state = 1

    def open_network(self, ip: str, port: int, timeout_ms: int, throw_excp_on_timeout: bool):
        """Equivalent of VB `Open(ip As String, port As UShort, timeoutMs As UInteger, throwExcpOnTimeout As Boolean)`."""
        if ip is None:
            raise ValueError("ip(addr)")
        ip = ip.strip()
        if len(ip) == 0:
            raise ValueError("please input IP addr")
        if port == 0:
            raise ValueError("port can not be 0")
        if self._state != 0:
            raise RuntimeError("reader is already opened")

        self._reset_handle()
        state, handle = API.OpenNetConnection(ip, port, timeout_ms)
        if state != ReaderException.ERROR_SUCCESS:
            self._handler = None
            if state == ReaderException.ERROR_CMD_COMM_TIMEOUT and not throw_excp_on_timeout:
                return
            raise IOError(f"IP'{ip}' port {port} connecting fail")
        self._handler = handle
        self._ip = ip
        self._net_port = port
        self._state = 2

    def open_usb(self, index: int):
        """Equivalent of VB `Open(index As UShort)` (HID/USB)."""
        if self._state != 0:
            raise RuntimeError("Reader is already open")

        self._reset_handle()
        state, handle = API.OpenHidConnection(index)
        if state != ReaderException.ERROR_SUCCESS:
            self._handler = None
            if state == ReaderException.ERROR_CMD_COMM_TIMEOUT:
                print(f"[DEBUG] open_usb: timeout opening index={index}, "
                      f"reader stays closed (this matches the original VB behaviour)",
                      file=sys.stderr)
                return
        self._handler = handle
        self._usbindex = index
        self._state = 3
        # Small settle delay: some HID devices need a brief pause after the
        # handle is opened before they reliably answer the first command.
        time.sleep(0.2)
        print(f"[DEBUG] open_usb: done. state={state}, self._state={self._state}, "
              f"self._handler={self._handler}", file=sys.stderr)

    def close(self):
        self._reset_handle()
        self._state = 0

    def cfhid_get_usb_count(self) -> int:
        return API.CFHid_GetUsbCount()

    def cfhid_get_usb_info(self, index: int, buffer: bytearray) -> bool:
        return API.CFHid_GetUsbInfo(index, buffer)

    def get_device_para(self) -> Devicepara:
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state, info = API.GetDevicePara(self._handler)
        if state == ReaderException.ERROR_SUCCESS:
            return info
        raise ReaderException(state)

    def set_device_para(self, info: Devicepara):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.SetDevicePara(self._handler, info)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def set_rf_tx_power(self, tx_power: int, reserved: int = 0):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.SetRFPower(self._handler, tx_power, reserved)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def release_relay(self, time: int):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.Release_Relay(self._handler, time)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def close_relay(self, time: int):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.Close_Relay(self._handler, time)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def inventory(self, inv_count: int, inv_param: int):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.InventoryContinue(self._handler, inv_count, inv_param)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def inventory_stop(self, timeout_ms: int):
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state = API.InventoryStop(self._handler, timeout_ms)
        if state == ReaderException.ERROR_SUCCESS:
            return
        raise ReaderException(state)

    def get_tag_uii(self, timeout_ms: int):
        """Returns a TagItem, or None if there is currently no tag / inventory ended."""
        if self._state == 0:
            raise RuntimeError("Reader is not open")
        state, info = API.GetTagUii(self._handler, timeout_ms)
        if state == ReaderException.ERROR_CMD_NO_TAG:
            return None
        if state == ReaderException.ERROR_SUCCESS:
            return TagItem(info=info)
        raise ReaderException(state)
