import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

class SerialThread(QThread):
    """串口通信线程"""
    data_received = pyqtSignal(bytes)
    error_occurred = pyqtSignal(str)

    def __init__(self, port, baudrate, bytesize, parity, stopbits):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.serial = None
        self.is_running = False

    def run(self):
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=0.1
            )
            self.is_running = True
            
            # 打开串口后立即清空输入缓冲区，防止波特率切换产生的垃圾数据导致解码错误
            if self.serial.is_open:
                self.serial.reset_input_buffer()

            while self.is_running and self.serial and self.serial.is_open:
                try:
                    if self.serial.in_waiting > 0:
                        data = self.serial.read(self.serial.in_waiting)
                        if data:
                            self.data_received.emit(data)
                except Exception as e:
                    self.error_occurred.emit(f"读取错误: {str(e)}")
                    break

        except Exception as e:
            self.error_occurred.emit(f"串口被占用: {str(e)}")

    def write_data(self, data):
        """发送数据"""
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(data)
                return len(data)
            except Exception as e:
                self.error_occurred.emit(f"发送错误: {str(e)}")
                return 0
        return 0

    def set_baudrate(self, baudrate):
        """动态设置波特率"""
        self.baudrate = baudrate
        if self.serial and self.serial.is_open:
            try:
                self.serial.baudrate = baudrate
                # 清除输入缓冲区以避免波特率不匹配产生的垃圾数据
                self.serial.reset_input_buffer()
                return True
            except Exception as e:
                self.error_occurred.emit(f"设置波特率失败: {str(e)}")
                return False
        return False

    def stop(self):
        """停止线程"""
        self.is_running = False
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.wait(1000)
