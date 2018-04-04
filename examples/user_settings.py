import datetime, time, logging

PYBPOD_API_LOG_LEVEL = logging.INFO
PYBPOD_API_LOG_LEVEL = logging.DEBUG #logging.WARNING; logging.DEBUG
PYBPOD_API_LOG_FILE  = 'pybpod-api.log'


WORKSPACE_PATH 	= 'BPOD-WORKSPACE'

PROTOCOL_NAME 	= datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')

SERIAL_PORT 	= '/dev/ttyACM0'
#SERIAL_PORT 	= '/dev/tty.usbmodem3174431'



BPOD_BNC_PORTS_ENABLED 		= [True, True]
BPOD_WIRED_PORTS_ENABLED 	= [True, True]
BPOD_BEHAVIOR_PORTS_ENABLED = [True, True, True, False, False, False, False, False]