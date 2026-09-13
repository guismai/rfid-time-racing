"""
Exception type raised when the UHF reader DLL returns a non-zero
status code. Carries the raw error code plus a human readable
message resolved from a lookup table.
"""


class ReaderException(Exception):
    """Exception raised for errors reported by the UHF reader device/DLL."""

    ERROR_SUCCESS = 0x0
    ERROR_HANDLE_ERROR = -255
    ERROR_OPEN_FAILED = -254
    ERROR_DLL_INNER_ERROR = -253
    ERROR_CMD_PARAM_ERROR = -252
    ERROR_CMD_SERIAL_NUM_EXISTS = -251
    ERROR_CMD_INTERNAL_ERROR = -250
    ERROR_CMD_NO_TAG = -249
    ERROR_CMD_TAG_RESP_TIMEOUT = -248
    ERROR_CMD_TAG_RESP_COLLISION = -247
    ERROR_CMD_CODE_OVERFLOW = -246
    ERROR_CMD_AUTH_FAILED = -245
    ERROR_CMD_PWD_ERROR = -244
    ERROR_CMD_SAM_NO_RESP = -243
    ERROR_CMD_SAM_CMD_FAILED = -242
    ERROR_CMD_RESP_FORMAT_ERROR = -241
    ERROR_CMD_HAS_MORE_DATA = -240
    ERROR_CMD_BUF_OVERFLOW = -239
    ERROR_CMD_COMM_TIMEOUT = -238
    ERROR_CMD_COMM_WRITE_FAILED = -237
    ERROR_CMD_COMM_READ_FAILED = -236
    ERROR_CMD_NOMORE_DATA = -235
    ERROR_DLL_UNCONNECT = -234
    ERROR_DLL_DISCONNECT = -233
    ERROR_CMD_RESP_CRC_ERROR = -232
    ERROR_CMD_IAP_CRC_ERR = -231
    ERROR_CMD_DOWMLOAD_ERR = -230
    ERROR_CMD_DOWM_NONE_ERR = -229

    _ERRORS = {
        ERROR_SUCCESS: "Command executed successfully",
        ERROR_HANDLE_ERROR: "Incorrect handle or parameter",
        ERROR_OPEN_FAILED: "Failed to open the reader",
        ERROR_DLL_INNER_ERROR: "Internal dynamic library error",
        ERROR_CMD_RESP_FORMAT_ERROR: "The reader responds to a data format error",
        ERROR_CMD_RESP_CRC_ERROR: "The reader responded to a CRC check error",
        ERROR_CMD_BUF_OVERFLOW: "The incoming cache is too small and the data overflows",
        ERROR_CMD_COMM_TIMEOUT: "Waiting for reader response timed out",
        ERROR_CMD_COMM_WRITE_FAILED: "An error occurred writing data to the reader",
        ERROR_CMD_COMM_READ_FAILED: "An error occurred while reading data from the reader",
        ERROR_CMD_HAS_MORE_DATA: "Subsequent data transmission is not complete",
        ERROR_DLL_UNCONNECT: "The network connection has not been established",
        ERROR_DLL_DISCONNECT: "The network connection has been disconnected",
        ERROR_CMD_IAP_CRC_ERR: "Download data verification error",
        ERROR_CMD_DOWMLOAD_ERR: "Data download error, data write error",
        ERROR_CMD_DOWM_NONE_ERR: "Data download failed. Procedure",
    }

    def __init__(self, error_code: int = ERROR_SUCCESS, message: str = None):
        if message is None:
            message = self.message_from_error_code(error_code)
        super().__init__(message)
        self.error_code = error_code

    @staticmethod
    def message_from_error_code(error_code: int) -> str:
        msg = ReaderException._ERRORS.get(error_code)
        if msg is not None:
            return msg
        return f"Unknown error code: {error_code}"
