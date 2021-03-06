# !/usr/bin/python3
# -*- coding: utf-8 -*-

import logging
from serial.serialutil import SerialException

from confapp import conf as settings
from pybpodapi.bpod.bpod_base import BpodBase
from pybpodapi.bpod.hardware.channels import ChannelType
from pybpodapi.bpod_modules.bpod_module import BpodModule
from pybpodapi.com.arcom import ArCOM, ArduinoTypes
from pybpodapi.com.protocol.recv_msg_headers import ReceiveMessageHeader
from pybpodapi.com.protocol.send_msg_headers import SendMessageHeader
from pybpodapi.exceptions.bpod_error import BpodErrorException

logger = logging.getLogger(__name__)


class BpodCOMProtocol(BpodBase):
    """
    Define command actions that can be requested to Bpod device.

    **Private attributes**

        _arcom
            :class:`pybpodapi.com.arcom.ArCOM`

            ArCOM object that performs serial communication.

    **Methods**

    """

    def __init__(self, serial_port=None, sync_channel=None, sync_mode=None):
        super(BpodCOMProtocol, self).__init__(serial_port, sync_channel, sync_mode)

        self._arcom = None  # type: ArCOM
        self._arcom_secondary = None  # type: ArCOM
        self._arcom_analog = None  # type: ArCOM
        self.bpod_com_ready = False

        # used to keep the list of msg ids sent using the load_serial_message function
        self.msg_id_list = [False for i in range(255)]

        if self.serial_port:
            # When self.serial_port is given (either in the settings file or during Bpod object init),
            # assume that the Bpod being used precedes version r2+ (i.e. machine_type < 4, AKA with only one USB serial port).
            self.open()
        else:
            # When no serial port is given (either in the settings file or during Bpod object init),
            # assume that the Bpod being used is version r2+ (i.e. machine_type == 4, AKA with three USB serial ports).
            # So it is necessary to identify its three serial ports since the user will not know.
            logger.info("No serial port provided. Searching ports manually...")
            primary_port, secondary_port, analog_port = self._bpodcom_identify_USB_serial_ports()
            self.serial_port = primary_port
            self.secondary_serial_port = secondary_port
            self.analog_serial_port = analog_port
            self.open()

    def open(self):
        super(BpodCOMProtocol, self).open()
        self.bpod_com_ready = True

    def close(self):
        if self.bpod_com_ready:
            super(BpodCOMProtocol, self).close()
            self._arcom.close()
            if self._arcom_secondary is not None:
                self._arcom_secondary.close()
            if self._arcom_analog is not None:
                self._arcom_analog.close()
            self.bpod_com_ready = False

    def manual_override(self, channel_type, channel_name, channel_number, value):
        """
        Manually override a Bpod channel

        :param ChannelType channel_type: channel type input or output
        :param ChannelName channel_name: channel name like PWM, Valve, etc.
        :param channel_number:
        :param int value: value to write on channel
        """
        if channel_type == ChannelType.INPUT:
            input_channel_name = channel_name + str(channel_number)
            channel_number = self.hardware.channels.input_channel_names.index(input_channel_name)
            try:
                self._bpodcom_override_input_state(channel_number, value)
            except:
                raise BpodErrorException(
                    'Error using manual_override: {name} is not a valid channel name.'.format(name=channel_name))

        elif channel_type == ChannelType.OUTPUT:
            if channel_name == 'Serial':
                self._bpodcom_send_byte_to_hardware_serial(channel_number, value)

            else:
                try:
                    output_channel_name = channel_name + str(channel_number)
                    channel_number = self.hardware.channels.output_channel_names.index(output_channel_name)
                    self._bpodcom_override_digital_hardware_state(channel_number, value)
                except:
                    raise BpodErrorException('Error using manual_override: {name} is not a valid channel name.'.format(
                        name=output_channel_name))
        else:
            raise BpodErrorException('Error using manualOverride: first argument must be "Input" or "Output".')

    def _bpodcom_connect(self, serial_port, secondary_port=None, analog_port=None, baudrate=115200, timeout=1):
        """
        Connect to Bpod using serial connection

        :param str serial_port: serial port to connect
        :param str secondary_port [optional]: secondary serial port on Bpod version r2+ (machine type 4)
        :param str analog_port [optional]: analog serial port on Bpod version r2+ (machine type 4)
        :param int baudrate: baudrate for serial connection
        :param float timeout: timeout which controls the behavior of read()
        """
        logger.debug("Connecting on port: %s", serial_port)
        self._arcom = ArCOM().open(serial_port, baudrate, timeout)

        if secondary_port:
            logger.debug("Connecting to secondary port on %s", secondary_port)
            self._arcom_secondary = ArCOM().open(secondary_port, baudrate, timeout)

        if analog_port:
            logger.debug("Connecting to analog port on %s", analog_port)
            self._arcom_analog = ArCOM().open(analog_port, baudrate, timeout)

    def _bpodcom_disconnect(self):
        """
        Signal Bpod device to disconnect now
        """
        logger.debug("Requesting disconnect ('%s')", SendMessageHeader.DISCONNECT)

        self._arcom.write_char(SendMessageHeader.DISCONNECT)

        res = self._arcom.read_char() == ReceiveMessageHeader.DISCONNECT_OK

        logger.debug("Disconnect result (%s)", res)
        return res

    # def __bpodcom_check_com_ready(self):
    #    if not self.bpod_com_ready: self.open()

    def _bpodcom_handshake(self):
        """
        Test connectivity by doing an handshake

        :return: True if handshake received, False otherwise
        :rtype: bool
        """

        logger.debug("Requesting handshake ('%s')", SendMessageHeader.HANDSHAKE)
        self._arcom.write_char(SendMessageHeader.HANDSHAKE)

        response = self._arcom.read_char()  # Receive response
        logger.debug("Response command is: '%s'", response)

        return response == ReceiveMessageHeader.HANDSHAKE_OK

    def _bpodcom_handshake_secondary(self):
        """
        Test connectivity of the secondary serial port by doing a handshake.
        Only compatible with Bpod r2+ (machine_type == 4).

        :return: True if handshake received, False otherwise
        :rtype: bool
        """
        
        logger.debug("Requesting handshake for secondary serial port ('%s')", SendMessageHeader.SECONDARY_PORT_HANDSHAKE)
        self._arcom.write_char(SendMessageHeader.SECONDARY_PORT_HANDSHAKE)  # Send from the primary serial port.

        response = self._arcom_secondary.read_uint8()  # Read from the secondary serial port.
        logger.debug("Response: %s", response)

        return response == ReceiveMessageHeader.SECONDARY_PORT_HANDSHAKE_OK
    
    def _bpodcom_handshake_analog(self):
        """
        Test connectivity of the analog serial port by doing a handshake.
        Only compatible with Bpod r2+ (machine_type == 4).

        :return: True if handshake received, False otherwise
        :rtype: bool
        """
        
        logger.debug("Requesting handshake for analog serial port ('%s')", SendMessageHeader.ANALOG_PORT_HANDSHAKE)
        self._arcom.write_char(SendMessageHeader.ANALOG_PORT_HANDSHAKE)  # Send from the primary serial port.

        response = self._arcom_analog.read_uint8()  # Read from the analog serial port.
        logger.debug("Response: %s", response)

        return response == ReceiveMessageHeader.ANALOG_PORT_HANDSHAKE_OK
    
    def _bpodcom_firmware_version(self):
        """
        Request firmware and machine type from Bpod

        :return: firmware and machine type versions
        :rtype: int, int
        """

        logger.debug("Requesting firmware version ('%s')", SendMessageHeader.FIRMWARE_VERSION)

        self._arcom.write_char(SendMessageHeader.FIRMWARE_VERSION)

        fw_version = self._arcom.read_uint16()  # type: int
        machine_type = self._arcom.read_uint16()  # type: int

        logger.debug("Firmware version: %s", fw_version)
        logger.debug("Machine type: %s", machine_type)

        return fw_version, machine_type

    def _bpodcom_reset_clock(self):
        """
        Reset session clock
        """
        logger.debug("Resetting clock")

        self._arcom.write_char(SendMessageHeader.RESET_CLOCK)
        return self._arcom.read_uint8() == ReceiveMessageHeader.RESET_CLOCK_OK

    def _bpodcom_stop_trial(self):
        """
        Stops ongoing trial (We recommend using computer-side pauses between trials, to keep data uniform)
        """
        logger.debug("Pausing trial")
        self._arcom.write_char(SendMessageHeader.EXIT_AND_RETURN)

    def _bpodcom_pause_trial(self):
        """
        Pause ongoing trial (We recommend using computer-side pauses between trials, to keep data uniform)
        """
        logger.debug("Pausing trial")
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.PAUSE_TRIAL), 0])
        self._arcom.write_array(bytes2send)

    def _bpodcom_resume_trial(self):
        """
        Resumes ongoing trial (We recommend using computer-side pauses between trials, to keep data uniform)
        """
        logger.debug("Resume trial")
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.PAUSE_TRIAL), 1])
        self._arcom.write_array(bytes2send)

    def _bpodcom_get_timestamp_transmission(self):
        """
        Return timestamp transmission scheme
        """
        logger.debug("Get timestamp transmission")

        self._arcom.write_char(SendMessageHeader.GET_TIMESTAMP_TRANSMISSION)
        return self._arcom.read_byte()

    def _bpodcom_hardware_description(self, hardware):
        """
        Request hardware description from Bpod

        :param Hardware hardware: hardware
        """

        logger.debug("Requesting hardware description ('%s')...", SendMessageHeader.HARDWARE_DESCRIPTION)
        self._arcom.write_char(SendMessageHeader.HARDWARE_DESCRIPTION)

        max_states = self._arcom.read_uint16()  # type: int
        logger.debug("Read max states: %s", max_states)

        cycle_period = self._arcom.read_uint16()  # type: int
        logger.debug("Read cycle period: %s", cycle_period)

        max_serial_events = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of events per serial channel: %s", max_serial_events)

        if hardware.firmware_version > 22:
            serial_message_max_bytes = self._arcom.read_uint8()  # type: int
            logger.debug("Read max number of bytes allowed per serial message: %s", serial_message_max_bytes)
        else:
            serial_message_max_bytes = 3
        
        n_global_timers = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of global timers: %s", n_global_timers)

        n_global_counters = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of global counters: %s", n_global_counters)

        n_conditions = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of conditions: %s", n_conditions)

        n_inputs = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of inputs: %s", n_inputs)

        inputs = self._arcom.read_char_array(array_len=n_inputs)  # type: list(str)
        logger.debug("Read inputs: %s", inputs)

        n_outputs = self._arcom.read_uint8()  # type: int
        logger.debug("Read number of outputs: %s", n_outputs)

        outputs = self._arcom.read_char_array(array_len=n_outputs)  # type: list(str)
        logger.debug("Read outputs: %s", outputs)

        hardware.max_states = max_states
        hardware.cycle_period = cycle_period
        hardware.max_serial_events = max_serial_events
        hardware.serial_message_max_bytes = serial_message_max_bytes
        hardware.n_global_timers = n_global_timers
        hardware.n_global_counters = n_global_counters
        hardware.n_conditions = n_conditions
        hardware.inputs = inputs
        hardware.outputs = outputs  # + ['G', 'G', 'G']

        hardware.n_uart_channels = len([idx for idx in inputs if idx == "U"])
        hardware.n_flex_channels = len([idx for idx in inputs if idx == "F"])

        hardware.live_timestamps = self._bpodcom_get_timestamp_transmission()

    def _bpodcom_enable_ports(self, hardware):
        """
        Enable input ports on Bpod device

        :param list[int] inputs_enabled: list of inputs to be enabled (0 = disabled, 1 = enabled)
        :rtype: bool
        """

        ###### set inputs enabled or disabled #######################################################
        hardware.inputs_enabled = [0] * len(hardware.inputs)

        for j, i in enumerate(hardware.bnc_inputports_indexes):
            hardware.inputs_enabled[i] = settings.BPOD_BNC_PORTS_ENABLED[j]

        for j, i in enumerate(hardware.wired_inputports_indexes):
            hardware.inputs_enabled[i] = settings.BPOD_WIRED_PORTS_ENABLED[j]

        for j, i in enumerate(hardware.behavior_inputports_indexes):
            hardware.inputs_enabled[i] = settings.BPOD_BEHAVIOR_PORTS_ENABLED[j]
        
        for j, i in enumerate(hardware.flex_inputports_indexes):
            hardware.inputs_enabled[i] = settings.BPOD_FLEX_PORTS_ENABLED[j]
        #############################################################################################

        logger.debug("Requesting ports enabling ('%s')", SendMessageHeader.ENABLE_PORTS)
        logger.debug("Inputs enabled (%s): %s", len(hardware.inputs_enabled), hardware.inputs_enabled)

        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.ENABLE_PORTS)] + hardware.inputs_enabled)

        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int

        logger.debug("Response: %s", response)

        return response == ReceiveMessageHeader.ENABLE_PORTS_OK
    
    def _bpodcom_set_sync_channel_and_mode(self, sync_channel, sync_mode):
        """
        Request sync channel and sync mode configuration

        :param int sync_channel: 255 = no sync, otherwise set to a hardware channel number
        :param int sync_mode: 0 = flip logic every trial, 1 = every state
        :rtype: bool
        """

        logger.debug("Requesting sync channel and mode ('%s')", SendMessageHeader.SYNC_CHANNEL_MODE)

        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SYNC_CHANNEL_MODE), sync_channel, sync_mode])

        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int

        logger.debug("Response: %s", response)

        return response == ReceiveMessageHeader.SYNC_CHANNEL_MODE_OK

    def _bpodcom_echo_softcode(self, softcode):
        """
        Send soft code
        """
        logger.debug("Echo softcode")
        self._arcom.write_char(SendMessageHeader.ECHO_SOFTCODE)
        self._arcom.write_char(softcode)

        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.ECHO_SOFTCODE), softcode])
        self._arcom.write_array(bytes2send)

    def _bpodcom_manual_override_exec_event(self, event_index, event_data):
        """
        Send soft code
        """
        logger.debug("Manual override execute virtual event")
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.MANUAL_OVERRIDE_EXEC_EVENT), event_index, event_data])
        self._arcom.write_array(bytes2send)

    def _bpodcom_override_input_state(self, channel_number, value):
        """
        Manually set digital value on channel

        :param int channel_number: number of Bpod port
        :param int value: value to be written
        """
        logger.debug("Override input state")

        bytes2send = ArduinoTypes.get_uint8_array(
            [ord(SendMessageHeader.MANUAL_OVERRIDE_EXEC_EVENT), channel_number, value])
        self._arcom.write_array(bytes2send)

    def _bpodcom_send_softcode(self, softcode):
        """
        Send soft code
        """
        logger.debug("Send softcode")
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.TRIGGER_SOFTCODE), softcode])
        self._arcom.write_array(bytes2send)

    def _bpodcom_send_state_machine(self, message):
        """
        Sends state machine to Bpod

        :param list(int) message: TODO
        :param list(int) ThirtyTwoBitMessage: TODO
        """
        # self.__bpodcom_check_com_ready()

        self._arcom.write_array(message)

    def _bpodcom_run_state_machine(self):
        """
        Request to run state machine now
        """
        # self.__bpodcom_check_com_ready()

        logger.debug("Requesting state machine run ('%s')", SendMessageHeader.RUN_STATE_MACHINE)

        self._arcom.write_char(SendMessageHeader.RUN_STATE_MACHINE)

    def _bpodcom_get_trial_timestamp_start(self):
        data = self._arcom.read_bytes_array(8)
        self.trial_start_micros = ArduinoTypes.cvt_uint64(b''.join(data))
        return self.trial_start_micros / float(self.hardware.DEFAULT_FREQUENCY_DIVIDER)

    def _bpodcom_read_trial_start_timestamp_seconds(self):
        """
        A new incoming timestamp message is available. Read trial start timestamp in millseconds and convert to seconds.

        :return: trial start timestamp in milliseconds
        :rtype: float
        """
        response = self._arcom.read_uint32()  # type: int

        # print('response', response)
        # logger.debug("Received start trial timestamp in millseconds: %s", response)

        # trial_start_timestamp = response / 1000.0

        return response * self.hardware.times_scale_factor

    def _bpodcom_read_timestamps(self):

        data = self._arcom.read_bytes_array(12)

        n_hw_timer_cyles = ArduinoTypes.cvt_uint32(b''.join(data[:4]))
        trial_end_micros = ArduinoTypes.cvt_uint64(b''.join(data[4:12]))  # / float(self.hardware.DEFAULT_FREQUENCY_DIVIDER)
        trial_end_timestamp = trial_end_micros / float(self.hardware.DEFAULT_FREQUENCY_DIVIDER)
        trial_time_from_micros = trial_end_timestamp - self.trial_start_timestamp
        trial_time_from_cycles = n_hw_timer_cyles/self.hardware.cycle_frequency
        discrepancy = abs(trial_time_from_micros - trial_time_from_cycles)*1000

        return trial_end_timestamp, discrepancy

    def _bpodcom_state_machine_installation_status(self):
        """
        Confirm if new state machine was correctly installed

        :rtype: bool
        """
        # self.__bpodcom_check_com_ready()

        response = self._arcom.read_uint8()  # type: int

        logger.debug("Read state machine installation status: %s", response)

        return response == ReceiveMessageHeader.STATE_MACHINE_INSTALLATION_STATUS

    def data_available(self):
        """
        Finds out if there is data received from Bpod

        :rtype: bool
        """
        return self._arcom.bytes_available() > 0

    def _bpodcom_read_opcode_message(self):
        """
        A new incoming opcode message is available. Read opcode code and data.

        :return: opcode and data
        :rtype: tuple(int, int)
        """
        response = self._arcom.read_uint8_array(array_len=2)
        opcode = response[0]
        data = response[1]

        logger.debug("Received opcode message: opcode=%s, data=%s", opcode, data)

        return opcode, data

    def _bpodcom_read_alltimestamps(self):
        """
        A new incoming timestamps message is available.
        Read number of timestamps to be sent and then read timestamps array.

        :return: timestamps array
        :rtype: list(float)
        """
        n_timestamps = self._arcom.read_uint16()  # type: int

        timestamps = self._arcom.read_uint32_array(array_len=n_timestamps)

        logger.debug("Received timestamps: %s", timestamps)

        return timestamps

    def _bpodcom_read_current_events(self, n_events):
        """
        A new incoming events message is available.
        Read number of timestamps to be sent and then read timestamps array.

        :param int n_events: number of events to read
        :return: a list with events
        :rtype: list(int)
        """
        current_events = self._arcom.read_uint8_array(array_len=n_events)

        logger.debug("Received current events: %s", current_events)

        return current_events

    def _bpodcom_read_event_timestamp(self):
        v = self._arcom.read_uint32()
        return float(v) * self.hardware.times_scale_factor

    def _bpodcom_load_serial_message(self, serial_channel, message_id, serial_message, n_messages, max_bytes):
        """
        Load serial message on channel

        :param TODO
        :rtype: bool
        """
        # self.__bpodcom_check_com_ready()

        if isinstance(serial_channel, BpodModule):
            serial_channel = serial_channel.serial_port

        self.msg_id_list[message_id] = True

        if len(serial_message) > max_bytes:
            raise BpodErrorException("Error: Serial messages cannot be more than {0} bytes in length.".format(max_bytes))

        if not (1 <= message_id <= 255):
            raise BpodErrorException('Error: Bpod can only store 255 serial messages (indexed 1-255). You used the message_id {0}'.format(message_id))

        message_container = [serial_channel-1, n_messages, message_id, len(serial_message)] + serial_message

        logger.debug("Requesting load serial message ('%s')", SendMessageHeader.LOAD_SERIAL_MESSAGE)
        logger.debug("Message: %s", message_container)

        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.LOAD_SERIAL_MESSAGE)] + message_container)

        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int

        logger.debug("Confirmation: %s", response)

        return response == ReceiveMessageHeader.LOAD_SERIAL_MESSAGE_OK

    def _bpodcom_reset_serial_messages(self):
        """
        Reset serial messages on Bpod device

        :rtype: bool
        """
        logger.debug("Requesting serial messages reset ('%s')", SendMessageHeader.RESET_SERIAL_MESSAGES)

        self._arcom.write_char(SendMessageHeader.RESET_SERIAL_MESSAGES)

        response = self._arcom.read_uint8()  # type: int

        logger.debug("Confirmation: %s", response)

        return response == ReceiveMessageHeader.RESET_SERIAL_MESSAGES

    def _bpodcom_override_digital_hardware_state(self, channel_number, value):
        """
        Manually set digital value on channel

        :param int channel_number: number of Bpod port
        :param int value: value to be written
        """

        bytes2send = ArduinoTypes.get_uint8_array(
            [ord(SendMessageHeader.OVERRIDE_DIGITAL_HW_STATE), channel_number, value])
        self._arcom.write_array(bytes2send)

    def _bpodcom_send_byte_to_hardware_serial(self, channel_number, value):
        """
        Send byte to hardware serial channel 1-3

        :param int channel_number:
        :param int value: value to be written
        """
        bytes2send = ArduinoTypes.get_uint8_array(
            [ord(SendMessageHeader.SEND_TO_HW_SERIAL), channel_number, value]
        )
        self._arcom.write_array(bytes2send)

    def _bpodcom_set_flex_channel_types(self, channel_types):
        """
        Configure channel types for Flex channels on Bpod r2+ (machine_type 4).
        
        :param list[int] channel_types: Channel types are: 0 = DI, 1 = DO, 2 = ADC, 3 = DAC
        :rtype: bool
        """

        logger.debug("Setting Flex channel types ('%s')", SendMessageHeader.SET_FLEX_CHANNEL_TYPES)
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SET_FLEX_CHANNEL_TYPES)] + channel_types)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.SET_FLEX_CHANNEL_TYPES_OK)

    def _bpodcom_get_flex_channel_types(self, n_flex_channels):
        """
        Read the current Flex channel types from the Bpod r2+ (machine_type 4)
        :rtype: list[int]
        """

        logger.debug("Requesting current flex channel types ('%s')", SendMessageHeader.GET_FLEX_CHANNEL_TYPES)
        self._arcom.write_char(SendMessageHeader.GET_FLEX_CHANNEL_TYPES)
        
        flex_channel_types = self._arcom.read_uint8_array(n_flex_channels)
        logger.debug("Read current Flex channel types: %s", flex_channel_types)
        
        return flex_channel_types
    
    def _bpodcom_set_analog_input_sampling_interval(self, sampling_interval):
        """
        Set the sampling interval for all flex channels configured as analog input. Compatible only with Bpod r2+ (machine type 4).
        
        Example: If the Bpod's state machine timer period is 100 microseconds (as is the case with the Bpod r2+) and the sampling_interval
        parameter is given a value of 10 cycles, then the analog input channels will be sampled once every 10 clock cycles of the state machine
        timer, resulting in a sampling frequency of ( 1 / (100 us/cycle * 10 cycles) ) = 1000 Hz.

        :param int sampling_interval: Interval at which to sample analog input flex channels. Units are state machine clock cycles.
        :rtype bool
        """
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SET_ANALOG_INPUT_SAMPLING_INTERVAL)])
        bytes2send += ArduinoTypes.get_uint32_array([sampling_interval])

        logger.debug("Setting analog input sampling interval ('%s')", SendMessageHeader.SET_ANALOG_INPUT_SAMPLING_INTERVAL)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.SET_ANALOG_INPUT_SAMPLING_INTERVAL_OK)
    
    def _bpodcom_set_analog_input_thresholds(self, thresholds_1, thresholds_2):
        """
        Set the analog input thresholds for all flex channels (regardless of whether they are currently configured as analog inputs).
        Each analog input channel has two thresholds that can each be set by the user. Compatible only with Bpod r2+ (machine type 4).

        :param list[int] thresholds_1: List of the first threshold values for each channel. Units are bits ranging from 0 to 4095.
        :param list[int] thresholds_2: List of the second threshold values for each channel. Units are bits ranging from 0 to 4095.
        :rtype bool
        """
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SET_ANALOG_INPUT_THRESHOLDS)])
        bytes2send += ArduinoTypes.get_uint16_array(thresholds_1 + thresholds_2)
        
        logger.debug("Setting analog input thresholds ('%s')", SendMessageHeader.SET_ANALOG_INPUT_THRESHOLDS)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.SET_ANALOG_INPUT_THRESHOLDS_OK)

    def _bpodcom_set_analog_input_threshold_polarity(self, polarity_1, polarity_2):
        """
        Set the analog input threshold polarity for both thresholds on all flex channels. Compatible only with Bpod r2+ (machine type 4).
        Polarity of 0 indicates to trigger once the analog input value becomes greater than the threshold.
        Polarity of 1 indicates to trigger once the analog input value becomes less than the threshold.

        :param list[int] polarity_1: List of polarities of the first threshold for each channel. Value is 0 or 1.
        :param list[int] polarity_2: List of polarities of the second threshold for each channel. Value is 0 or 1.
        :rtype bool
        """
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SET_ANALOG_INPUT_THRESHOLD_POLARITY)] + polarity_1 + polarity_2)
        
        logger.debug("Setting analog input threshold polarity ('%s')", SendMessageHeader.SET_ANALOG_INPUT_THRESHOLD_POLARITY)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.SET_ANALOG_INPUT_THRESHOLD_POLARITY_OK)

    def _bpodcom_set_analog_input_threshold_mode(self, modes):
        """
        Set the analog input threshold mode for all flex channels. Compatible only with Bpod r2+ (machine type 4).
        When mode is set to 0, each threshold for that channel becomes disabled once it is triggered and must be re-enabled by the state machine.
        When mode is set to 1, each threshold for that channel re-enables the other threshold before becoming disabled itself once it is triggered.

        :param list[int] modes: List of modes for each channel. Value is 0 or 1.
        :rtype bool
        """
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.SET_ANALOG_INPUT_THRESHOLD_MODE)] + modes)
        
        logger.debug("Setting analog input threshold mode ('%s')", SendMessageHeader.SET_ANALOG_INPUT_THRESHOLD_MODE)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.SET_ANALOG_INPUT_THRESHOLD_MODE_OK)

    def _bpodcom_enable_analog_input_threshold(self, channel, threshold, value):
        """
        Enable an analog input threshold for a given flex channel. Compatible only with Bpod r2+ (machine type 4).

        :param int channel: Index of flex channel (0 - 3).
        :param int threshold: Index of threshold (0 or 1).
        :param int value: Disabled = 0, Enabled = 1.
        :rtype bool
        """
        bytes2send = ArduinoTypes.get_uint8_array([ord(SendMessageHeader.ENABLE_ANALOG_INPUT_THRESHOLD), channel, threshold, value])

        logger.debug("Enabling analog input threshold ('%s')", SendMessageHeader.ENABLE_ANALOG_INPUT_THRESHOLD)
        self._arcom.write_array(bytes2send)

        response = self._arcom.read_uint8()  # type: int
        logger.debug("Response: %s", response)

        return (response == ReceiveMessageHeader.ENABLE_ANALOG_INPUT_THRESHOLD_OK)
    
    def _bpodcom_read_analog_input_samples(self, n_channels):
        """
        While running a trial, any channels configured as analog inputs return samples to the PC via the analog serial port.
        The data format on that port is: [TrialNumber, uint16] [Sample Ch0, uint16]...[Sample ChN, uint16].
        Channels 0-N are not necessarily physical channels 0-N. Only channels configured as analog inputs return data, in rank order
        (so if channels 2 and 4 are configured as analog inputs, you'd have:
        [TrialNumber] [Sample1 from Ch2] [Sample1 from Ch4] [TrialNumber] [Sample2 from Ch2] [Sample2 from Ch4]...etc.
        TrialNumber is reset at the beginning of each behavior session with op '*' on the state machine's primary port.

        :param int n_channels: number of flex channels configured as analog input.
        :return: List of samples for each analog input channel in uint16, including trial number with each sample.
        :rtype list[int]:
        """
        n_bytes_available = self._arcom_analog.bytes_available()
        if n_bytes_available > 0:
            n_samples = int(n_bytes_available / (2 * (n_channels + 1)))  # Add 1 to n_channels to account for the trial number, which must be read before the sample(s). Each sample and trial number is uint16 which is 2 bytes.
            if n_samples > 0:
                msg = self._arcom_analog.read_uint16_array((n_samples * (n_channels + 1)))
                return msg
        return []  # return an empty list if nothing to be read.
    
    def _bpodcom_identify_USB_serial_ports(self):
        """
        Identify the Bpod r2+ (machine_type == 4) primary, secondary, and analog serial ports.
        The Bpod r2+ creates 3 separate USB serial ports on the PC. The first handles everything that the single port handled previously.
        The second is available for a second application to send event bytes to the state machine (e.g. Bonsai, running on the same PC).
        The third port is dedicated for receiving analog data. For the auto-identification routine, open the COM ports and listen for bytes.
        The primary port will send '222' every 100ms (as in firmware v22). Once the primary port is found, open the remaining ports and
        send '{' to the primary port. The secondary application port will respond with '222'. Open the remaining port(s) and send '}' to
        the primary port to receive '223' from the analog input port.

        :rtype: three-tuple
        :return: primary port, secondary port, analog port
        """
        available_ports = ArCOM.list_ports()
        logger.debug("Available USB serial ports: %s", available_ports)

        primary_port = None
        secondary_port = None
        analog_port = None
        bad_ports = []
        
        if not self.serial_port:
            # This means that no serial port was given during Bpod object init, nor in the settings file. So try to find it.
            for port in available_ports:
                try:
                    logger.debug("Testing primary port using: %s", port)
                    test_connect = ArCOM().open(serial_port=port, baudrate=self.baudrate, timeout=1)
                    reading = test_connect.read_uint8()
                    if (reading == ReceiveMessageHeader.PRIMARY_PORT_PING):  # Bpod writes 0xDE every 100ms on its primary COM port. 0xDE in decimal is 222 which refers to firmware version 22.
                        logger.debug("Primary port is: %s", port)
                        primary_port = port
                        test_connect.close()
                        break
                    else:
                        logger.debug("Nothing received from port: %s", port)
                        test_connect.close()

                except SerialException:
                    logger.debug("Bad port: %s", port)
                    bad_ports.append(port)
        else:
            primary_port = self.serial_port  # This means that a serial port was given either during Bpod object init or in the settings file. So use it.
        
        if primary_port:  # Check if it was found from the previous for loop.
            available_ports.remove(primary_port)  # remove from available ports to avoid testing it again.
            for port in bad_ports:
                available_ports.remove(port)
            
            primary_connection = ArCOM().open(serial_port=primary_port, baudrate=self.baudrate, timeout=1)
            for port in available_ports:
                try:
                    if not secondary_port or not analog_port:
                        logger.debug("Testing secondary and analog ports using: %s", port)
                        test_connect = ArCOM().open(serial_port=port, baudrate=self.baudrate, timeout=1)
                        primary_connection.write_char(SendMessageHeader.SECONDARY_PORT_HANDSHAKE)  # Send handshake byte for both the secondary serial port
                        primary_connection.write_char(SendMessageHeader.ANALOG_PORT_HANDSHAKE)     # and the analog serial port.
                        
                        response = test_connect.read_uint8()  # The response will determine whether the current port is the secondary or analog serial port.
                        if (response == ReceiveMessageHeader.SECONDARY_PORT_HANDSHAKE_OK):
                            logger.debug("Secondary port is: %s", port)
                            secondary_port = port
                            test_connect.close()
                        elif (response == ReceiveMessageHeader.ANALOG_PORT_HANDSHAKE_OK):
                            logger.debug("Analog port is: %s", port)
                            analog_port = port
                            test_connect.close()
                        else:
                            logger.debug("Nothing received from port: %s", port)
                            test_connect.close()
                    else:
                        break  # Break out of for loop once both the secondary and analog ports were found.

                except SerialException:
                    logger.debug("Bad port: %s", port)
                    bad_ports.append(port)

            primary_connection.close()
            
        return primary_port, secondary_port, analog_port
    
    @property
    def hardware(self):
        # self.__bpodcom_check_com_ready()
        return BpodBase.hardware.fget(self)  # type: Hardware

    @property
    def modules(self):
        # self.__bpodcom_check_com_ready()
        return BpodBase.modules.fget(self)

    # @property
    # def inputs(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.inputs.fget(self)

    # @property
    # def outputs(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.outputs.fget(self)

    # @property
    # def channels(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.channels.fget(self)

    # @property
    # def max_states(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.max_states.fget(self)

    # @property
    # def max_serial_events(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.max_serial_events.fget(self)

    # @property
    # def inputs_enabled(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.inputs_enabled.fget(self)

    # @property
    # def cycle_period(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.cycle_period.fget(self)

    # @property
    # def n_global_timers(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.n_global_timers.fget(self)

    # @property
    # def n_global_counters(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.n_global_counters.fget(self)

    # @property
    # def n_conditions(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.n_conditions.fget(self)

    # @property
    # def n_uart_channels(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.n_uart_channels.fget(self)

    # @property
    # def firmware_version(self):
    #   return BpodBase.firmware_version.fget(self)

    # @property
    # def machine_type(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.machine_type.fget(self)

    # @property
    # def cycle_frequency(self):
    #   self.__bpodcom_check_com_ready()
    #   return BpodBase.cycle_frequency.fget(self)
